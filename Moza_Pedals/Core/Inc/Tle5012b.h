/*
 * tle5012b.h
 *
 * Bit-banged SSC (3-wire SPI-like) driver for the Infineon TLE5012B-E1000
 * magnetic angle sensor, for STM32F767ZI / HAL.
 *
 * Shared bus (all sensors):
 *   SCK    -> PA5  (GPIO output, idles LOW)
 *   DATA   -> PB15 (GPIO, switched between output/input at runtime)
 *
 * Per-sensor CS (active LOW) - select which physical sensor a call
 * addresses by passing one of the instances below:
 *   TLE5012_GEARBOX     CSQ -> PD14
 *   TLE5012_THROTTLE    CSQ -> PA4
 *   TLE5012_CLUTCH      CSQ -> PB6
 *   TLE5012_BRAKE_HALL  CSQ -> PB7
 *
 * See the accompanying chat explanation for the full protocol description.
 */

#ifndef TLE5012B_H
#define TLE5012B_H

#include "stm32f7xx_hal.h"
#include <stdint.h>

/* ---------------------------------------------------------------------
 * Command word bit fields (TLE5012B datasheet Table 17, "Command Word")
 * bit15    RW    : 0 = write, 1 = read
 * bit14:11 LOCK  : 0000 default access (addr 0x00-0x04), 1010 config access
 * bit10    UPD   : 0 = current value, 1 = update-buffer value
 * bit9:4   ADDR  : 6-bit register address
 * bit3:0   ND    : number of data words to transfer (0 = single reg, no
 *                  safety word; 1..15 = that many words + safety word)
 * ------------------------------------------------------------------- */
#define TLE5012_CMD_READ        0x8000U
#define TLE5012_CMD_WRITE       0x0000U

#define TLE5012_REG_STAT        (0x00U << 4)   /* Status register        */
#define TLE5012_REG_ACSTAT      (0x01U << 4)   /* Activation status      */
#define TLE5012_REG_AVAL        (0x02U << 4)   /* Angle value (AVAL)     */
#define TLE5012_REG_ASPD        (0x03U << 4)   /* Angle speed            */
#define TLE5012_REG_AREV        (0x04U << 4)   /* Angle revolution       */

#define TLE5012_ND(n)            ((uint16_t)(n) & 0x0FU)

/* Read AVAL, 1 data word + safety word appended  =>  0x8021 */
#define TLE5012_CMD_READ_ANGLE   (TLE5012_CMD_READ | TLE5012_REG_AVAL | TLE5012_ND(1))

typedef struct {
    uint16_t raw_data;     /* AVAL register: bit15 = status/roll bit,
                               bits14..0 = signed 15-bit angle           */
    uint16_t safety_word;  /* STAT bits / RESP / CRC (see datasheet 4.4.1.2) */
    uint8_t  crc_ok;       /* 1 if the 8-bit J1850 CRC in safety_word matched */
} TLE5012_Frame_t;

/* Identifies one physical TLE5012B on the shared SCK/DATA bus by its
 * dedicated CS (chip-select) port/pin. */
typedef struct {
    GPIO_TypeDef *cs_port;
    uint16_t      cs_pin;
} TLE5012_Sensor_t;

extern const TLE5012_Sensor_t TLE5012_GEARBOX;
extern const TLE5012_Sensor_t TLE5012_THROTTLE;
extern const TLE5012_Sensor_t TLE5012_CLUTCH;
extern const TLE5012_Sensor_t TLE5012_BRAKE_HALL;

void              TLE5012_GPIO_Init(void);
HAL_StatusTypeDef TLE5012_ReadRegister(const TLE5012_Sensor_t *sensor,
                                        uint16_t command,
                                        TLE5012_Frame_t *frame);
float             TLE5012_ConvertAngleDeg(uint16_t raw_data);
HAL_StatusTypeDef TLE5012_ReadAngleDeg(const TLE5012_Sensor_t *sensor,
                                        float *angle_deg,
                                        TLE5012_Frame_t *frame);

#endif /* TLE5012B_H */
