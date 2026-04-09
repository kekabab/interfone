# The following lines of boilerplate have to be in your project's CMakeLists
# in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

# Include ESP-ADF components if ADF_PATH is set
if(DEFINED ENV{ADF_PATH})
    set(EXTRA_COMPONENT_DIRS $ENV{ADF_PATH}/components)
else()
    message(FATAL_ERROR "Please set ADF_PATH to your esp-adf directory!")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32-ai-intercom)
