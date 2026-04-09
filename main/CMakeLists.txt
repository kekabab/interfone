idf_component_register(SRCS "app_main.c" "wifi_setup.c"
                       INCLUDE_DIRS "."
                       EMBED_FILES "ola_esp32.raw" "minuto_esp32.raw"
                       REQUIRES esp_websocket_client nvs_flash audio_pipeline audio_stream audio_board audio_hal audio_sal esp_peripherals fatfs esp-adf-libs)
