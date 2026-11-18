TARGET := demo_fw
BUILD_DIR := build
CC := gcc
SIZE := size

DEMO_INJECT_WARNING ?= 1
DEMO_INJECT_ERROR ?= 0

CFLAGS := -Wall -Wextra -g3 -O0
ifeq ($(DEMO_INJECT_WARNING),1)
CFLAGS += -DDEMO_INJECT_WARNING
endif
ifeq ($(DEMO_INJECT_ERROR),1)
CFLAGS += -DDEMO_INJECT_ERROR
endif

ELF := $(BUILD_DIR)/$(TARGET).elf
OBJ := $(BUILD_DIR)/main.o

all: $(ELF)
	$(SIZE) $(ELF)

$(BUILD_DIR):
	mkdir -p $@

$(OBJ): main.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJ)
	$(CC) $^ -o $@

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean
