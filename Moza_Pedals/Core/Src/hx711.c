/*
 * hx711.c
 *
 * Bit-banged driver for the HX711 24-bit ADC (load cell amplifier),
 * for STM32F767ZI / HAL.
 * PA1 = SCK (output), PA0 = DOUT (input).
 *
 * Timing is far looser than the TLE5012B's SSC bus (HX711 min pulse
 * width is on the order of 0.2us, and it tolerates being bit-banged
 * quite slowly), so this driver just uses small fixed-cycle delays
 * instead of DWT-based precision timing.
 */

#include "hx711.h"

#define SCK_PORT   GPIOA
#define SCK_PIN    GPIO_PIN_1
#define DOUT_PORT  GPIOA
#define DOUT_PIN   GPIO_PIN_0

#define SCK_HIGH()   (SCK_PORT->BSRR = SCK_PIN)
#define SCK_LOW()    (SCK_PORT->BSRR = (uint32_t)SCK_PIN << 16U)
#define DOUT_READ()  (((DOUT_PORT->IDR & DOUT_PIN) != 0U) ? 1U : 0U)

/* Small fixed delay between clock edges (~1us range). HX711 needs
 * SCK high/low pulses of at least ~0.2us and total 24-bit read well
 * under its 60us power-down timeout, so this is comfortably safe. */
static inline void hx711_delay(void)
{
    for (volatile uint32_t i = 0; i < 40U; i++) {
        __NOP();
    }
}

void HX711_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    __HAL_RCC_GPIOA_CLK_ENABLE();

    HAL_GPIO_WritePin(SCK_PORT, SCK_PIN, GPIO_PIN_RESET);

    gpio.Pin   = SCK_PIN;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(SCK_PORT, &gpio);

    gpio.Pin  = DOUT_PIN;
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_PULLUP;   /* HX711 DOUT is open-drain-ish; safe default */
    HAL_GPIO_Init(DOUT_PORT, &gpio);
}

uint8_t HX711_IsReady(void)
{
    /* DOUT goes LOW when a conversion result is ready to be read */
    return (DOUT_READ() == 0U) ? 1U : 0U;
}

int32_t HX711_ReadRaw(HX711_Gain_t gain)
{
    uint32_t raw = 0;

    /* Wait for conversion ready (DOUT low). Caller can avoid blocking
     * here by checking HX711_IsReady() first if desired. */
    while (DOUT_READ() != 0U) {
        /* spin */
    }

    /* Clock out 24 data bits, MSB first */
    for (uint8_t i = 0; i < 24U; i++) {
        SCK_HIGH();
        hx711_delay();
        raw = (raw << 1) | DOUT_READ();
        SCK_LOW();
        hx711_delay();
    }

    /* Extra pulses select gain/channel for the *next* conversion */
    for (uint8_t i = 0; i < ((uint8_t)gain - 24U); i++) {
        SCK_HIGH();
        hx711_delay();
        SCK_LOW();
        hx711_delay();
    }

    /* Sign-extend 24-bit two's complement value into int32_t */
    if ((raw & 0x800000U) != 0U) {
        raw |= 0xFF000000U;
    }

    return (int32_t)raw;
}

int32_t HX711_ReadMedian(HX711_Gain_t gain, uint8_t n)
{
    int32_t samples[15];

    if (n == 0U) {
        n = 1U;
    }
    if (n > 15U) {
        n = 15U;
    }

    for (uint8_t i = 0; i < n; i++) {
        samples[i] = HX711_ReadRaw(gain);
    }

    /* Simple insertion sort - n is small (<=15), no need for anything
     * fancier. */
    for (uint8_t i = 1; i < n; i++) {
        int32_t key = samples[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && samples[j] > key) {
            samples[j + 1] = samples[j];
            j--;
        }
        samples[j + 1] = key;
    }

    return samples[n / 2U];
}
