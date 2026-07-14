/*
 * Interfone AI — Firmware ESP32-A1S (Gemini Live, full-duplex)
 *
 * Fluxo (nova arquitetura com IA em tempo real):
 *   1. Campainha -> envia TRIGGER_CALL ao servidor e toca "Olá" localmente.
 *   2. Servidor abre a sessão Gemini Live e responde PLAY_LIVE_START:24000.
 *   3. ESP32 abre player contínuo (24kHz) + recorder contínuo (16kHz) AO MESMO TEMPO
 *      (full-duplex). O mic envia chunks PCM 16kHz continuamente; o alto-falante
 *      recebe chunks PCM 24kHz da IA continuamente.
 *   4. Quando o morador aperta um botão, o servidor envia PLAY_LIVE_END (para o
 *      player live), PLAY_RESPONSE:<rate>:<file> + áudio .raw, e PLAY_DONE.
 *      Durante esse playback o mic é pausado (evita eco do .raw).
 *   5. Ao final, o servidor encerra a sessão.
 *
 * Regra de ouro (STATE_OF_PROJECT.md): NUNCA chamar i2s_driver_install manualmente
 * nem usar uninstall_drv=false nos streams — o ESP-ADF gerencia o I2S. Cumprida.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/i2s.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "board.h"
#include "audio_hal.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "audio_pipeline.h"
#include "audio_element.h"
#include "audio_common.h"
#include "i2s_stream.h"
#include "raw_stream.h"
#include "es8388.h"

#include "wifi_setup.h"

// Áudios embutidos (gerados a 24kHz, PCM 16-bit mono)
extern const uint8_t ola_start[] asm("_binary_ola_esp32_raw_start");
extern const uint8_t ola_end[]   asm("_binary_ola_esp32_raw_end");
extern const uint8_t minuto_start[] asm("_binary_minuto_esp32_raw_start");
extern const uint8_t minuto_end[]   asm("_binary_minuto_esp32_raw_end");

#define TAG "INTERFONE_AI"

// GPIOs do botão de campainha (mantidas do firmware original)
#define BOTAO_1 GPIO_NUM_36
#define BOTAO_2 GPIO_NUM_13
#define BOTAO_3 GPIO_NUM_23
#define BOTAO_4 GPIO_NUM_5
#define BOTAO_5 GPIO_NUM_18

// Sample rates
#define REC_SAMPLE_RATE 16000      // Gemini Live exige 16kHz na entrada
#define LIVE_PLAY_RATE  24000      // Gemini Live fala em 24kHz
#define GREETING_RATE   24000      // ola_esp32.raw e minuto_esp32.raw são 24kHz
#define I2S_PORT        I2S_NUM_0

esp_websocket_client_handle_t ws_client;

// ── Estado da aplicação ──
typedef enum {
    ST_IDLE,
    ST_GREETING,        // tocando "Olá" antes da IA assumir
    ST_LIVE,            // full-duplex: mic + player contínuos com a IA
    ST_PLAYING_RAW,     // tocando um .raw de resposta rápida (mic pausado)
    ST_HANGUP,          // chamada encerrada, voltando ao idle
} app_state_t;

static volatile app_state_t g_state = ST_IDLE;

// Pipelines ESP-ADF
static audio_element_handle_t raw_read = NULL;    // saída do recorder
static audio_element_handle_t raw_write_live = NULL;  // entrada do player live (24kHz)
static audio_element_handle_t raw_write_raw = NULL;   // entrada do player de .raw
static audio_pipeline_handle_t recorder = NULL;
static audio_pipeline_handle_t player_live = NULL;    // player contínuo (IA)
static audio_pipeline_handle_t player_raw = NULL;     // player de .raw (respostas rápidas)

// Task handles
static TaskHandle_t rec_task_handle = NULL;

// ── Constrói o pipeline de RECORDER (mic -> raw) a 16kHz ──
static esp_err_t recorder_pipeline_open(void) {
    if (recorder) return ESP_OK;  // já aberto (full-duplex mantém aberto)

    audio_pipeline_cfg_t pipeline_cfg = DEFAULT_AUDIO_PIPELINE_CONFIG();
    recorder = audio_pipeline_init(&pipeline_cfg);

    i2s_stream_cfg_t i2s_cfg = I2S_STREAM_CFG_DEFAULT();
    i2s_cfg.type = AUDIO_STREAM_READER;
    i2s_cfg.i2s_config.sample_rate = REC_SAMPLE_RATE;
    i2s_cfg.i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
    audio_element_handle_t i2s_reader = i2s_stream_init(&i2s_cfg);

    audio_element_info_t i2s_info = {0};
    audio_element_getinfo(i2s_reader, &i2s_info);
    i2s_info.bits = 16;
    i2s_info.channels = 1;
    i2s_info.sample_rates = REC_SAMPLE_RATE;
    audio_element_setinfo(i2s_reader, &i2s_info);

    raw_stream_cfg_t raw_cfg = RAW_STREAM_CFG_DEFAULT();
    raw_cfg.type = AUDIO_STREAM_READER;
    raw_read = raw_stream_init(&raw_cfg);
    audio_element_set_output_timeout(raw_read, portMAX_DELAY);

    audio_pipeline_register(recorder, i2s_reader, "i2s");
    audio_pipeline_register(recorder, raw_read, "raw");
    const char *link[2] = {"i2s", "raw"};
    audio_pipeline_link(recorder, link, 2);
    audio_pipeline_run(recorder);

    ESP_LOGI(TAG, "Recorder pipeline aberto (PCM 16k Mono)");
    return ESP_OK;
}

static void recorder_pipeline_close(void) {
    if (recorder) {
        audio_pipeline_stop(recorder);
        audio_pipeline_wait_for_stop(recorder);
        audio_pipeline_deinit(recorder);
        recorder = NULL;
        raw_read = NULL;
    }
}

// ── Constrói o PLAYER contínuo (raw -> i2s) a 24kHz para a IA ──
static esp_err_t player_live_pipeline_open(int rate) {
    if (player_live) {
        // reabre com novo rate se mudou
        audio_pipeline_stop(player_live);
        audio_pipeline_wait_for_stop(player_live);
        audio_pipeline_deinit(player_live);
        player_live = NULL;
        raw_write_live = NULL;
    }
    audio_pipeline_cfg_t pipeline_cfg = DEFAULT_AUDIO_PIPELINE_CONFIG();
    player_live = audio_pipeline_init(&pipeline_cfg);

    raw_stream_cfg_t raw_cfg = RAW_STREAM_CFG_DEFAULT();
    raw_cfg.type = AUDIO_STREAM_WRITER;
    raw_write_live = raw_stream_init(&raw_cfg);

    i2s_stream_cfg_t i2s_cfg = I2S_STREAM_CFG_DEFAULT();
    i2s_cfg.type = AUDIO_STREAM_WRITER;
    // A1S usa uma única porta I2S para DAC e ADC. O primeiro stream precisa
    // instalar TX+RX; o leitor do microfone reutiliza esse mesmo driver.
    i2s_cfg.i2s_config.mode = I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX;
    i2s_cfg.i2s_config.sample_rate = rate;
    i2s_cfg.i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
    audio_element_handle_t i2s_writer = i2s_stream_init(&i2s_cfg);

    audio_pipeline_register(player_live, raw_write_live, "raw");
    audio_pipeline_register(player_live, i2s_writer, "i2s");
    const char *link[2] = {"raw", "i2s"};
    audio_pipeline_link(player_live, link, 2);
    audio_pipeline_run(player_live);

    ESP_LOGI(TAG, "Player LIVE aberto (PCM %dHz Mono)", rate);
    return ESP_OK;
}

static void player_live_pipeline_close(void) {
    if (player_live) {
        audio_pipeline_stop(player_live);
        audio_pipeline_wait_for_stop(player_live);
        audio_pipeline_deinit(player_live);
        player_live = NULL;
        raw_write_live = NULL;
    }
}

// ── Constrói o PLAYER de .raw (respostas rápidas) com rate variável ──
static esp_err_t player_raw_pipeline_open(int rate) {
    if (player_raw) {
        audio_pipeline_stop(player_raw);
        audio_pipeline_wait_for_stop(player_raw);
        audio_pipeline_deinit(player_raw);
        player_raw = NULL;
        raw_write_raw = NULL;
    }
    audio_pipeline_cfg_t pipeline_cfg = DEFAULT_AUDIO_PIPELINE_CONFIG();
    player_raw = audio_pipeline_init(&pipeline_cfg);

    raw_stream_cfg_t raw_cfg = RAW_STREAM_CFG_DEFAULT();
    raw_cfg.type = AUDIO_STREAM_WRITER;
    raw_write_raw = raw_stream_init(&raw_cfg);

    i2s_stream_cfg_t i2s_cfg = I2S_STREAM_CFG_DEFAULT();
    i2s_cfg.type = AUDIO_STREAM_WRITER;
    i2s_cfg.i2s_config.sample_rate = rate;
    i2s_cfg.i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
    audio_element_handle_t i2s_writer = i2s_stream_init(&i2s_cfg);

    audio_pipeline_register(player_raw, raw_write_raw, "raw");
    audio_pipeline_register(player_raw, i2s_writer, "i2s");
    const char *link[2] = {"raw", "i2s"};
    audio_pipeline_link(player_raw, link, 2);
    audio_pipeline_run(player_raw);

    ESP_LOGI(TAG, "Player RAW aberto (PCM %dHz Mono)", rate);
    return ESP_OK;
}

static void player_raw_pipeline_close(void) {
    if (player_raw) {
        audio_pipeline_stop(player_raw);
        audio_pipeline_wait_for_stop(player_raw);
        audio_pipeline_deinit(player_raw);
        player_raw = NULL;
        raw_write_raw = NULL;
    }
}

// ── Tarefa de gravação contínua: lê o mic e envia ao servidor ──
// Pára quando g_state sai de ST_LIVE (ou ST_GREETING pós-saudação).
static void recorder_task(void *arg) {
    char buf[2048];
    uint32_t sent_chunks = 0;
    uint32_t sent_bytes = 0;
    ESP_LOGI(TAG, "[REC] Captura direta do ADC no I2S full-duplex (16kHz)...");

    // O player Live ja instalou o unico driver I2S do A1S em TX+RX. Ler dele
    // diretamente evita um segundo i2s_stream/driver ADF concorrendo pelo ADC.
    while (g_state == ST_LIVE) {
        size_t read_len = 0;
        esp_err_t err = i2s_read(I2S_PORT, buf, sizeof(buf), &read_len, portMAX_DELAY);
        if (g_state != ST_LIVE) {
            break;
        }
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "[REC] Erro I2S ao ler microfone: %s", esp_err_to_name(err));
            vTaskDelay(10 / portTICK_PERIOD_MS);
            continue;
        }
        if (read_len == 0) {
            continue;
        }

        int peak = 0;
        for (size_t i = 0; i + 1 < read_len; i += 2) {
            int sample = ((int16_t *)buf)[i / 2];
            if (sample < 0) sample = -sample;
            if (sample > peak) peak = sample;
        }

        if (esp_websocket_client_is_connected(ws_client)) {
            int sent = esp_websocket_client_send_bin(ws_client, buf, read_len, portMAX_DELAY);
            if (sent > 0) {
                sent_chunks++;
                sent_bytes += sent;
                if ((sent_chunks % 50) == 1) {
                    ESP_LOGI(TAG, "[REC] Enviados %u bytes em %u blocos (pico=%d)", sent_bytes, sent_chunks, peak);
                }
            } else {
                ESP_LOGW(TAG, "[REC] Falha ao enviar audio: %d", sent);
            }
        }
    }
    ESP_LOGI(TAG, "[REC] Captura continua encerrada.");
    rec_task_handle = NULL;
    vTaskDelete(NULL);
}
static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;

    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "[WS] Conectado ao servidor!");
    } else if (event_id == WEBSOCKET_EVENT_ERROR) {
        ESP_LOGE(TAG, "[WS] Erro na conexão.");
    } else if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "[WS] Desconectado.");
        // Volta ao idle se cair durante uma sessão
        if (g_state == ST_LIVE || g_state == ST_PLAYING_RAW) {
            g_state = ST_IDLE;
            player_live_pipeline_close();
            player_raw_pipeline_close();
        }
    } else if (event_id == WEBSOCKET_EVENT_DATA) {
        if (data->op_code == 1 && data->data_len > 0) {  // texto
            char payload[128] = {0};
            int len = data->data_len < (int)sizeof(payload) - 1 ? data->data_len : (int)sizeof(payload) - 1;
            strncpy(payload, data->data_ptr, len);
            ESP_LOGI(TAG, "[SERVER CMD] %s", payload);

            // PLAY_LIVE_START:<rate>  -> abre player contínuo + mic contínuo (full-duplex)
            if (strncmp(payload, "PLAY_LIVE_START:", 16) == 0) {
                int rate = LIVE_PLAY_RATE;
                sscanf(payload + 16, "%d", &rate);
                ESP_LOGI(TAG, "Iniciando modo LIVE (player %dHz + mic 16kHz)", rate);

                // Garante estado limpo de players de .raw
                player_raw_pipeline_close();

                // Abre player contínuo da IA
                player_live_pipeline_open(rate);

                // O player já abriu o I2S em TX+RX; inicia a captura direta do ADC.
                g_state = ST_LIVE;
                if (rec_task_handle == NULL) {
                    xTaskCreate(recorder_task, "rec_live", 8192, NULL, 5, &rec_task_handle);
                }
            }
            // PLAY_LIVE_END -> para o player contínuo (a IA parou de falar / morador agiu)
            else if (strcmp(payload, "PLAY_LIVE_END") == 0) {
                ESP_LOGI(TAG, "Pausando player LIVE para resposta rapida.");
                player_live_pipeline_close();
                // Keep ST_LIVE here. PLAY_RESPONSE performs the transition to
                // ST_PLAYING_RAW; END_SESSION is the terminal state change.
            }
            // PLAY_RESPONSE:<rate>:<file> -> toca um .raw (resposta rápida)
            else if (strncmp(payload, "PLAY_RESPONSE:", 14) == 0) {
                int rate = 8000;
                sscanf(payload + 14, "%d:", &rate);
                ESP_LOGI(TAG, "Tocando .raw de resposta (rate %d)", rate);

                // Pausa o mic: muda estado para a task de gravação parar de enviar
                if (g_state == ST_LIVE) {
                    g_state = ST_PLAYING_RAW;
                }
                // Fecha o player live para liberar o I2S pro player de .raw
                player_live_pipeline_close();
                // Abre o player de .raw no rate informado
                player_raw_pipeline_open(rate);
            }
            // PLAY_DONE -> fim do .raw; se voltamos pro live, reabre; senão idle
            else if (strcmp(payload, "PLAY_DONE") == 0) {
                ESP_LOGI(TAG, "Fim do .raw de resposta.");
                player_raw_pipeline_close();
                // O servidor encerra a sessão após o .raw, então voltamos ao idle.
                g_state = ST_IDLE;
            }
            // END_SESSION -> servidor encerrou a sessão Gemini
            else if (strcmp(payload, "END_SESSION") == 0) {
                ESP_LOGI(TAG, "Servidor encerrou a sessão.");
                g_state = ST_IDLE;
                player_live_pipeline_close();
                player_raw_pipeline_close();
            }
        } else if (data->op_code == 2 && data->data_len > 0) {  // binário
            // Áudio da IA -> player live (em ST_LIVE)
            if (g_state == ST_LIVE && raw_write_live) {
                static uint32_t live_rx_chunks = 0;
                static uint32_t live_rx_bytes = 0;
                int written = raw_stream_write(raw_write_live, (char *)data->data_ptr, data->data_len);
                if (written > 0) {
                    live_rx_chunks++;
                    live_rx_bytes += written;
                    if ((live_rx_chunks % 50) == 1) {
                        ESP_LOGI(TAG, "[LIVE] Recebidos %u bytes da IA em %u blocos", live_rx_bytes, live_rx_chunks);
                    }
                } else {
                    ESP_LOGW(TAG, "[LIVE] Falha ao colocar áudio da IA no player: %d", written);
                }
            }
            // Áudio .raw de resposta -> player de .raw (em ST_PLAYING_RAW)
            else if (g_state == ST_PLAYING_RAW && raw_write_raw) {
                raw_stream_write(raw_write_raw, (char *)data->data_ptr, data->data_len);
            }
        }
    }
}

// ── app_main ──
void app_main(void) {
    esp_log_level_set("*", ESP_LOG_INFO);
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, "  INTERFONE AI — GEMINI LIVE (FULL-DUPLEX)        ");
    ESP_LOGI(TAG, "==================================================");

    ESP_ERROR_CHECK(nvs_flash_init());

    // Codec / placa de áudio
    audio_board_handle_t board_handle = audio_board_init();
    audio_hal_ctrl_codec(board_handle->audio_hal, AUDIO_HAL_CODEC_MODE_BOTH, AUDIO_HAL_CTRL_START);
    audio_hal_set_volume(board_handle->audio_hal, 75);

    es8388_config_adc_input(0x50);
    es8388_write_reg(0x12, 0xBB);  // ALC Enable
    es8388_write_reg(0x13, 0x10);
    es8388_write_reg(0x14, 0x32);
    es8388_write_reg(0x10, 0x00);

    // Wi-Fi (credenciais no wifi_setup / hardcoded do projeto original)
    wifi_init_sta("Vozona", "26121935");

    // WebSocket cliente (buffer maior pra suportar streaming contínuo)
    esp_websocket_client_config_t ws_cfg = {
        .uri = "wss://interfone.onrender.com/ws/esp32",
        .port = 443,
        .transport = WEBSOCKET_TRANSPORT_OVER_SSL,
        .buffer_size = 8192,
        .keep_alive_enable = true,
        .keep_alive_interval = 10,
    };
    ws_client = esp_websocket_client_init(&ws_cfg);
    esp_websocket_register_events(ws_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, (void *)ws_client);
    esp_websocket_client_start(ws_client);

    // GPIOs dos botões de campainha
    gpio_config_t io_conf = {
        .intr_type = GPIO_PIN_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = ((1ULL << BOTAO_1) | (1ULL << BOTAO_2) | (1ULL << BOTAO_3) | (1ULL << BOTAO_4) | (1ULL << BOTAO_5)),
        .pull_down_en = 0,
        .pull_up_en = 1,
    };
    gpio_config(&io_conf);

    // Loop principal: detecta campainha
    bool already_triggered = false;
    while (1) {
        bool bell = (gpio_get_level(BOTAO_1) == 0 || gpio_get_level(BOTAO_2) == 0 ||
                     gpio_get_level(BOTAO_3) == 0 || gpio_get_level(BOTAO_4) == 0 ||
                     gpio_get_level(BOTAO_5) == 0);

        // Borda de descida: só dispara uma vez por toque
        if (bell && !already_triggered && g_state == ST_IDLE) {
            already_triggered = true;
            ESP_LOGI(TAG, "🔔 Campainha pressionada! → IA assume imediatamente.");

            // Avisa o servidor para abrir a sessão Gemini Live.
            // A IA cumprimenta o visitante ("Olá, com quem gostaria de falar?") assim
            // que a sessão começa — não há saudação local nem gravação intermedária.
            if (esp_websocket_client_is_connected(ws_client)) {
                esp_websocket_client_send_text(ws_client, "TRIGGER_CALL", 12, portMAX_DELAY);
            }
        } else if (!bell) {
            already_triggered = false;
        }

        vTaskDelay(100 / portTICK_PERIOD_MS);
    }
}
