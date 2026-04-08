#include <stdio.h>
#include <string.h>
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
#include "filter_resample.h"
#include "es8388.h"

// Wi-fi module header
#include "wifi_setup.h"

extern const uint8_t ola_start[] asm("_binary_ola_esp32_raw_start");
extern const uint8_t ola_end[]   asm("_binary_ola_esp32_raw_end");

extern const uint8_t minuto_start[] asm("_binary_minuto_esp32_raw_start");
extern const uint8_t minuto_end[]   asm("_binary_minuto_esp32_raw_end");

#define BOTAO_PLAY GPIO_NUM_23
static const char *TAG = "INTERFONE_AI";
esp_websocket_client_handle_t ws_client;

#define RECORD_TIME_SEC 4
#define SAMPLE_RATE 8000        
#define CODEC_SAMPLE_RATE 8000  
#define CODEC_CHANNELS 1
#define TTS_SAMPLE_RATE 24000
#define I2S_PORT I2S_NUM_0

uint8_t *audio_buffer;
size_t audio_buffer_size = RECORD_TIME_SEC * SAMPLE_RATE * 2;

bool is_recording = false;
volatile bool stop_recording = false;
bool is_playing_response = false;
static audio_element_handle_t raw_read, raw_write;
static audio_pipeline_handle_t recorder, player;

static esp_err_t recorder_pipeline_open()
{
    audio_element_handle_t i2s_stream_reader;
    audio_pipeline_cfg_t pipeline_cfg = DEFAULT_AUDIO_PIPELINE_CONFIG();
    recorder = audio_pipeline_init(&pipeline_cfg);

    i2s_stream_cfg_t i2s_cfg = I2S_STREAM_CFG_DEFAULT();
    i2s_cfg.type = AUDIO_STREAM_READER;
    i2s_cfg.i2s_config.sample_rate = SAMPLE_RATE;
    i2s_cfg.i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
    i2s_stream_reader = i2s_stream_init(&i2s_cfg);
    
    audio_element_info_t i2s_info = {0};
    audio_element_getinfo(i2s_stream_reader, &i2s_info);
    i2s_info.bits = 16;
    i2s_info.channels = 1;
    i2s_info.sample_rates = SAMPLE_RATE; 
    audio_element_setinfo(i2s_stream_reader, &i2s_info);

    raw_stream_cfg_t raw_cfg = RAW_STREAM_CFG_DEFAULT();
    raw_cfg.type = AUDIO_STREAM_READER;
    raw_read = raw_stream_init(&raw_cfg);
    audio_element_set_output_timeout(raw_read, portMAX_DELAY);

    audio_pipeline_register(recorder, i2s_stream_reader, "i2s");
    audio_pipeline_register(recorder, raw_read, "raw");

    const char *link_tag[2] = {"i2s", "raw"};
    audio_pipeline_link(recorder, &link_tag[0], 2);

    audio_pipeline_run(recorder);
    ESP_LOGI(TAG, "Recorder pipeline created (PCM 8k Mono)");
    return ESP_OK;
}

void player_pipeline_open(int rate) {
    if (player) {
        audio_pipeline_stop(player);
        audio_pipeline_wait_for_stop(player);
        audio_pipeline_deinit(player);
        player = NULL;
        raw_write = NULL;
    }

    audio_pipeline_cfg_t pipeline_cfg = DEFAULT_AUDIO_PIPELINE_CONFIG();
    player = audio_pipeline_init(&pipeline_cfg);

    raw_stream_cfg_t raw_cfg = RAW_STREAM_CFG_DEFAULT();
    raw_cfg.type = AUDIO_STREAM_WRITER;
    raw_write = raw_stream_init(&raw_cfg);

    i2s_stream_cfg_t i2s_cfg = I2S_STREAM_CFG_DEFAULT();
    i2s_cfg.type = AUDIO_STREAM_WRITER;
    i2s_cfg.i2s_config.sample_rate = rate;
    i2s_cfg.i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
    audio_element_handle_t i2s_stream_writer = i2s_stream_init(&i2s_cfg);
    
    audio_pipeline_register(player, raw_write, "raw");
    audio_pipeline_register(player, i2s_stream_writer, "i2s");
    
    const char *link_tag[2] = {"raw", "i2s"};
    audio_pipeline_link(player, &link_tag[0], 2);
    
    audio_pipeline_run(player);
    ESP_LOGI(TAG, "Player pipeline created (PCM %dHz Mono)", rate);
}

// ── TAREFA DE GRAVAÇÃO (ADF PIPELINE) ──
static void recorder_pipeline_task(void *pvParameters) {
    if (recorder_pipeline_open() != ESP_OK) {
        vTaskDelete(NULL);
        return;
    }
    
    ESP_LOGI(TAG, "Lendo voz do visitante...");
    int64_t start_time = esp_timer_get_time();
    char buf[2048];

    // Grava por até 5 segundos ou até o morador interromper
    while (!stop_recording && (esp_timer_get_time() - start_time) < 5000000) {
        int read_len = raw_stream_read(raw_read, buf, sizeof(buf));
        if (read_len > 0 && esp_websocket_client_is_connected(ws_client)) {
            esp_websocket_client_send_bin(ws_client, buf, read_len, portMAX_DELAY);
        } else if (read_len < 0) {
            break;
        }
    }

    ESP_LOGI(TAG, "Finalizando gravação do visitante.");
    if (esp_websocket_client_is_connected(ws_client)) {
        esp_websocket_client_send_text(ws_client, "AUDIO_END", 9, portMAX_DELAY);
    }
    
    if (recorder) {
        audio_pipeline_stop(recorder);
        audio_pipeline_wait_for_stop(recorder);
        audio_pipeline_deinit(recorder);
        recorder = NULL;
    }
    raw_read = NULL;
    
    vTaskDelete(NULL);
}

static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    
    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "[WS] Conectado ao servidor Render!");
    } else if (event_id == WEBSOCKET_EVENT_ERROR) {
        ESP_LOGE(TAG, "[WS] Erro na conexão WebSocket.");
    } else if (event_id == WEBSOCKET_EVENT_DATA) {
        if (data->op_code == 1 && data->data_len > 0) { // Text Message
            char payload[128] = {0};
            int len = data->data_len < 127 ? data->data_len : 127;
            strncpy(payload, data->data_ptr, len);
            
            ESP_LOGI(TAG, "[SERVER CMD] %s", payload);
            
            if (strncmp(payload, "PLAY_RESPONSE:", 14) == 0) {
                int rate = 24000;
                sscanf(payload + 14, "%d:", &rate);
                ESP_LOGI(TAG, "Recebendo áudio (Rate: %d)...", rate);

                stop_recording = true; 
                is_playing_response = true;

                if (player) {
                    audio_pipeline_stop(player);
                    audio_pipeline_wait_for_stop(player);
                    audio_pipeline_deinit(player);
                    player = NULL;
                }
                player_pipeline_open(rate);
            } else if (strcmp(payload, "PLAY_DONE") == 0) {
                ESP_LOGI(TAG, "Fim da resposta. Fechando player.");
                if (player) {
                    audio_pipeline_stop(player);
                    audio_pipeline_wait_for_stop(player);
                    audio_pipeline_deinit(player);
                    player = NULL;
                }
                raw_write = NULL;
                is_playing_response = false;
            }
        } 
        else if (data->op_code == 2 && data->data_len > 0) { // Binary Message
            if (is_playing_response && raw_write) {
                raw_stream_write(raw_write, (char *)data->data_ptr, data->data_len);
            }
        }
    }
}

void app_main(void) {
    esp_log_level_set("*", ESP_LOG_INFO);
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, "  INTERFONE AI INTERATIVO (SEM VOIP)              ");
    ESP_LOGI(TAG, "==================================================");

    ESP_ERROR_CHECK(nvs_flash_init());

    audio_board_handle_t board_handle = audio_board_init();
    audio_hal_ctrl_codec(board_handle->audio_hal, AUDIO_HAL_CODEC_MODE_BOTH, AUDIO_HAL_CTRL_START);
    audio_hal_set_volume(board_handle->audio_hal, 75);

    es8388_config_adc_input(0x50);
    es8388_write_reg(0x12, 0xBB); // ALC Enable
    es8388_write_reg(0x13, 0x10); 
    es8388_write_reg(0x14, 0x32); 
    es8388_write_reg(0x10, 0x00); 


    wifi_init_sta("Vozona", "26121935");

    esp_websocket_client_config_t ws_cfg = {
        .uri = "wss://interfone.onrender.com/ws/esp32",
        .port = 443,
        .transport = WEBSOCKET_TRANSPORT_OVER_SSL,
        .buffer_size = 4096,
        .keep_alive_enable = true,
        .keep_alive_interval = 10,
    };
    ws_client = esp_websocket_client_init(&ws_cfg);
    esp_websocket_register_events(ws_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, (void *)ws_client);
    esp_websocket_client_start(ws_client);

    gpio_config_t io_conf = {
        .intr_type = GPIO_PIN_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = ((1ULL << GPIO_NUM_36) | (1ULL << GPIO_NUM_13) | (1ULL << GPIO_NUM_23) | (1ULL << GPIO_NUM_5) | (1ULL << GPIO_NUM_18)),
        .pull_down_en = 0,
        .pull_up_en = 1,
    };
    gpio_config(&io_conf);

    while (1) {
        if (gpio_get_level(GPIO_NUM_36) == 0 || gpio_get_level(GPIO_NUM_13) == 0 || gpio_get_level(GPIO_NUM_23) == 0 || gpio_get_level(GPIO_NUM_5) == 0 || gpio_get_level(GPIO_NUM_18) == 0) {
            
            ESP_LOGI(TAG, "Campainha pressionada!");
            
            // ── PASSO 1: CHAMA O CELULAR IMEDIATAMENTE ──
            if (esp_websocket_client_is_connected(ws_client)) {
                esp_websocket_client_send_text(ws_client, "TRIGGER_CALL", 12, portMAX_DELAY);
            }

            // ── PASSO 2: SAUDAÇÃO INICIAL ──
            ESP_LOGI(TAG, "Falando 'Olá' ao visitante...");
            player_pipeline_open(TTS_SAMPLE_RATE); 
            raw_stream_write(raw_write, (char *)ola_start, ola_end - ola_start);
            
            // Espera tempo fixo do áudio terminar (3 segundos)
            vTaskDelay(3000 / portTICK_PERIOD_MS);
            
            // Limpa o player antes de começar a gravar
            audio_pipeline_stop(player);
            audio_pipeline_deinit(player);
            player = NULL;
            raw_write = NULL;

            // ── PASSO 3: GRAVAÇÃO DO VISITANTE (PIPELINE) ──
            ESP_LOGI(TAG, "Ouvindo o visitante...");
            stop_recording = false;
            if (esp_websocket_client_is_connected(ws_client)) {
                esp_websocket_client_send_text(ws_client, "AUDIO_START", 11, portMAX_DELAY);
            }
            // Criamos a tarefa de gravação
            xTaskCreate(recorder_pipeline_task, "rec_task", 8192, NULL, 5, NULL);
            
            // Espera a gravação finalizar (6 segundos)
            vTaskDelay(6000 / portTICK_PERIOD_MS);

            // ── PASSO 4: SOM DE ESPERA ──
            ESP_LOGI(TAG, "Pedindo para aguardar...");
            player_pipeline_open(TTS_SAMPLE_RATE);
            raw_stream_write(raw_write, (char *)minuto_start, minuto_end - minuto_start);
            
            // Espera tempo expansivo (12 segundos) para englobar os beeps contínuos de espera!
            vTaskDelay(12000 / portTICK_PERIOD_MS);
            
            audio_pipeline_stop(player);
            audio_pipeline_deinit(player);
            player = NULL;
            raw_write = NULL;

            ESP_LOGI(TAG, "Sequência finalizada. Tudo pronto.");
        }
        vTaskDelay(100 / portTICK_PERIOD_MS);
    }
}
