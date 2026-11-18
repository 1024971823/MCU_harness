#include <stdint.h>
#include <stdio.h>

/* 模拟寄存器：真实 STM32H563 上这里是 0x42020014 之类的外设地址 */
static volatile uint32_t GPIOA_ODR;

static void led_toggle(void)
{
    GPIOA_ODR ^= (1u << 5);
}

int main(void)
{
#ifdef DEMO_INJECT_WARNING
    uint32_t unused_tick = 0;   /* -> warning: unused variable */
#endif

#ifdef DEMO_INJECT_ERROR
    led_tggle();                /* -> error: 拼写错误，函数未声明 */
#endif

    for (int i = 0; i < 4; i++) {
        led_toggle();
    }
    printf("GPIOA_ODR = 0x%08X\n", (unsigned)GPIOA_ODR);
    return 0;
}
