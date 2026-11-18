cmake_minimum_required(VERSION 3.16)
project(demo_fw C)

# 一个“假固件”工程：用主机 gcc 编译，让你在没有板子、没有交叉工具链时也能测试构建模块。
# 换成真实 STM32 工程时，只需传入 arm-none-eabi 工具链文件即可，Harness 代码无需改动。

option(DEMO_INJECT_ERROR   "故意制造一个编译错误" OFF)
option(DEMO_INJECT_WARNING "故意制造一个编译告警" ON)

add_executable(demo_fw main.c)
target_compile_options(demo_fw PRIVATE -Wall -Wextra)

# 模仿固件产物命名：demo_fw.elf
set_target_properties(demo_fw PROPERTIES SUFFIX ".elf")

if(DEMO_INJECT_ERROR)
  target_compile_definitions(demo_fw PRIVATE DEMO_INJECT_ERROR)
endif()
if(DEMO_INJECT_WARNING)
  target_compile_definitions(demo_fw PRIVATE DEMO_INJECT_WARNING)
endif()
