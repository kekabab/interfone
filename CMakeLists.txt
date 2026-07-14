# The following lines of boilerplate have to be in your project's CMakeLists
# in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

# ESP-ADF v2 inclui o componente opcional esp_coze, que requer palavras-chave
# CMake de uma versao mais nova que o ESP-IDF 4.2.5 desta placa. Carregamos
# os demais componentes ADF explicitamente para manter compatibilidade.
if(DEFINED ENV{ADF_PATH})
    set(EXTRA_COMPONENT_DIRS
        $ENV{ADF_PATH}/components/adf_utils
        $ENV{ADF_PATH}/components/audio_board
        $ENV{ADF_PATH}/components/audio_hal
        $ENV{ADF_PATH}/components/audio_mixer
        $ENV{ADF_PATH}/components/audio_pipeline
        $ENV{ADF_PATH}/components/audio_recorder
        $ENV{ADF_PATH}/components/audio_sal
        $ENV{ADF_PATH}/components/audio_stream
        $ENV{ADF_PATH}/components/battery_service
        $ENV{ADF_PATH}/components/bluetooth_service
        $ENV{ADF_PATH}/components/clouds
        $ENV{ADF_PATH}/components/coredump_upload_service
        $ENV{ADF_PATH}/components/display_service
        $ENV{ADF_PATH}/components/dueros_service
        $ENV{ADF_PATH}/components/esp-adf-libs
        $ENV{ADF_PATH}/components/esp-sr
        $ENV{ADF_PATH}/components/esp_actions
        $ENV{ADF_PATH}/components/esp_codec_dev
        $ENV{ADF_PATH}/components/esp_dispatcher
        $ENV{ADF_PATH}/components/esp_event_cast
        $ENV{ADF_PATH}/components/esp_peripherals
        $ENV{ADF_PATH}/components/input_key_service
        $ENV{ADF_PATH}/components/ota_service
        $ENV{ADF_PATH}/components/playlist
        $ENV{ADF_PATH}/components/tone_partition
        $ENV{ADF_PATH}/components/wifi_service
    )
else()
    message(FATAL_ERROR "Please set ADF_PATH to your esp-adf directory!")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32-ai-intercom)