/*
 * hx711.h
 *
 * Bit-banged driver for the HX711 24-bit ADC (load cell amplifier),
 * for STM32F767ZI / HAL.
 *
 * Wiring used by this driver:
 *   HX711 SCK  -> PA1  (GPIO output, idles LOW)
 *   HX711 DOUT -> PA0  (GPIO input, pulled up externally/internally,
 *                        goes LOW when a conversion result is ready)
 *
 * Protocol summary (HX711 datasheet):
 *   - DOUT idles HIGH while converting, goes LOW when data is ready.
 *   - Host then clocks SCK 24 times, reading one bit of the 24-bit
 *     two's-complement result on each falling edge (MSB first).
 *   - 1-3 extra clock pulses (25/26/27 total) select the gain/channel
 *     used for the *next* conversion:
 *       25 pulses -> Channel A, gain 128 (default, most common)
 *       26 pulses -> Channel B, gain 32
 *       27 pulses -> Channel A, gain 64
 *   - SCK must not stay high for > 60us or the HX711 powers down.
 */

#ifndef HX711_H
#define HX711_H

#include "stm32f7xx_hal.h"
#include <stdint.h>

typedef enum {
    HX711_CHA_GAIN_128 = 25,  /* Channel A, gain 128 - default */
    HX711_CHB_GAIN_32  = 26,  /* Channel B, gain 32             */
    HX711_CHA_GAIN_64  = 27   /* Channel A, gain 64             */
} HX711_Gain_t;

void    HX711_Init(void);
uint8_t HX711_TryRead(HX711_Gain_t gain, int32_t *out);
uint8_t HX711_IsReady(void);

/* Blocking: waits for DOUT to go low (conversion ready) then clocks out
 * the 24-bit result plus the gain-select pulses for the next read.
 * Returns a sign-extended 32-bit value. */
int32_t HX711_ReadRaw(HX711_Gain_t gain);

/* Takes `n` blocking raw reads (n <= 15), sorts them, and returns the
 * middle value. Rejects single-sample spikes far better than averaging;
 * feed the result into an EMA/low-pass filter for further smoothing.
 * NOTE: blocking - with a 10Hz HX711 this takes roughly n*100ms. */
int32_t HX711_ReadMedian(HX711_Gain_t gain, uint8_t n);

#endif /* HX711_H */
