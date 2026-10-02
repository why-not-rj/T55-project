/*
 * tle5012b.c
 *
 * Bit-banged SSC driver for the TLE5012B-E1000, STM32F767ZI / HAL.
 * PA5 = SCK, PA6 = DATA (bidirectional), PB6 = CSQ (active LOW).
 */

#include "Tle5012b.h"

/* ---------------------------------------------------------------------
 * Low level pin access (direct register access - fast, deterministic,
 * no HAL_GPIO_Init struct overhead inside the bit-bang loop)
 * ------------------------------------------------------------------- */
#define SCK_PORT    GPIOA
#define SCK_PIN     GPIO_PIN_5
#define DATA_PORT   GPIOA
#define DATA_PIN    GPIO_PIN_6
#define DATA_PIN_POS 6U                /* pin number, for MODER math    */
#define CS_PORT     GPIOB
#define CS_PIN      GPIO_PIN_6

#define SCK_HIGH()   (SCK_PORT->BSRR  = SCK_PIN)
#define SCK_LOW()    (SCK_PORT->BSRR  = (uint32_t)SCK_PIN  << 16U)
#define CS_LOW()     (CS_PORT->BSRR   = (uint32_t)CS_PIN   << 16U)
#define CS_HIGH()    (CS_PORT->BSRR   = CS_PIN)
#define DATA_SET()   (DATA_PORT->BSRR = DATA_PIN)
#define DATA_CLR()   (DATA_PORT->BSRR = (uint32_t)DATA_PIN << 16U)
#define DATA_READ()  (((DATA_PORT->IDR & DATA_PIN) != 0U) ? 1U : 0U)

/* Switch PA6 between push-pull output and floating/pull-up input      */
static inline void DATA_DirOutput(void)
{
    DATA_PORT->MODER = (DATA_PORT->MODER & ~(3UL << (DATA_PIN_POS * 2U)))
                                          |  (1UL << (DATA_PIN_POS * 2U));
}
static inline void DATA_DirInput(void)
{
    DATA_PORT->MODER &= ~(3UL << (DATA_PIN_POS * 2U));   /* 00b = input */
}

/* ---------------------------------------------------------------------
 * DWT-based busy-wait delay. All TLE5012B SSC minimum timings
 * (tSCKh=40ns, tSCKl=30ns, tCSs/tCSh=105ns, twr_delay=130ns,
 * tCSoff=600ns) are well below 1us, so a fixed ~300-500ns step keeps us
 * safely inside spec while staying far under the 8 Mbit/s push-pull
 * maximum (which we are not trying to hit with bit-banging anyway).
 * ------------------------------------------------------------------- */
static void DWT_Init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;

#if defined(DWT_LAR_KEY)
    /* Some Cortex-M7 revisions gate the DWT registers behind a lock
     * register - CTRL writes silently fail (CYCCNTENA never actually
     * sets) until this key is written. Harmless no-op on cores without
     * the lock. This is the #1 cause of bit_delay() hanging forever. */
    DWT->LAR = DWT_LAR_KEY;
#else
    *(volatile uint32_t *)0xE0001FB0UL = 0xC5ACCE55UL; /* DWT->LAR key */
#endif

    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;
}

static inline void bit_delay(void)
{
    /* ~300 ns step -> overall SCK period on the order of 1 us (~1 MHz) */
    uint32_t cycles = SystemCoreClock / 3333333U;
    uint32_t start  = DWT->CYCCNT;

    /* Bounded even if CYCCNT never moves (e.g. DWT not actually running):
     * cap the spin so a stalled counter degrades to "too fast" instead of
     * hanging forever. iter_cap is generous for any realistic core clock. */
    uint32_t iter_cap = 100000U;
    while (((DWT->CYCCNT - start) < cycles) && (iter_cap != 0U)) {
        iter_cap--;
    }
}

/* ---------------------------------------------------------------------
 * GPIO init - equivalent of the CubeMX configuration described in chat:
 *   PA5 (SCK)  : GPIO_Output, push-pull, no pull, idle LOW
 *   PA6 (DATA) : GPIO_Output, push-pull, no pull, idle state doesn't
 *                matter (redirected to input at the start of every
 *                response phase)
 *   PB6 (CSQ)  : GPIO_Output, push-pull, no pull, idle HIGH (deselected)
 * ------------------------------------------------------------------- */
void TLE5012_GPIO_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();

    HAL_GPIO_WritePin(SCK_PORT, SCK_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(CS_PORT,  CS_PIN,  GPIO_PIN_SET);      /* deselected */
    HAL_GPIO_WritePin(DATA_PORT, DATA_PIN, GPIO_PIN_RESET);

    gpio.Pin   = SCK_PIN;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    HAL_GPIO_Init(SCK_PORT, &gpio);

    gpio.Pin = CS_PIN;
    HAL_GPIO_Init(CS_PORT, &gpio);

    gpio.Pin = DATA_PIN;                 /* starts as output, we flip it
                                             to input at runtime          */
    HAL_GPIO_Init(DATA_PORT, &gpio);

    DWT_Init();
}

/* ---------------------------------------------------------------------
 * Single-bit primitives.
 * Per datasheet 4.4.1.2: "Data is put on the data line with the rising
 * edge on SCK and read with the falling edge on SCK" - whichever side is
 * currently transmitting changes DATA around the rising edge, and the
 * receiving side captures it at/just after the falling edge.
 * ------------------------------------------------------------------- */
static void WriteBit(uint8_t bit)
{
    if (bit) { DATA_SET(); } else { DATA_CLR(); }
    bit_delay();
    SCK_HIGH();                 /* rising edge: bit is asserted on DATA  */
    bit_delay();
    SCK_LOW();                  /* falling edge: sensor samples DATA here*/
    bit_delay();
}

static uint8_t ReadBit(void)
{
    uint8_t bit;
    SCK_HIGH();                 /* rising edge: sensor updates DATA      */
    bit_delay();
    SCK_LOW();                  /* falling edge: defined "read" point    */
    bit = DATA_READ();
    bit_delay();
    return bit;
}

static void WriteWord(uint16_t word)
{
    for (int8_t i = 15; i >= 0; i--) {
        WriteBit((uint8_t)((word >> i) & 0x1U));
    }
}

static uint16_t ReadWord(void)
{
    uint16_t word = 0;
    for (int8_t i = 15; i >= 0; i--) {
        word |= (uint16_t)(ReadBit() << i);
    }
    return word;
}

/* ---------------------------------------------------------------------
 * 8-bit CRC per datasheet 4.4.1.2 "Cyclic Redundancy Check":
 *   Generator polynomial X8+X4+X3+X2+1  (0x1D once the leading bit is
 *   dropped), seed 0xFF, remainder inverted before transmission.
 *   Computed over command word + data word(s) (every byte MSB first).
 * ------------------------------------------------------------------- */
static uint8_t TLE5012_CRC8(const uint8_t *data, uint8_t len)
{
    uint8_t crc = 0xFFU;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t b = 0; b < 8U; b++) {
            crc = (crc & 0x80U) ? (uint8_t)((crc << 1) ^ 0x1DU)
                                 : (uint8_t)(crc << 1);
        }
    }
    return (uint8_t)~crc;
}

/* ---------------------------------------------------------------------
 * Full SSC transaction: command word -> (turnaround) -> data word ->
 * safety word, all inside one CSQ-low window, exactly as shown in the
 * datasheet's "SSC data transfer (data-read example)" figure.
 * ------------------------------------------------------------------- */
HAL_StatusTypeDef TLE5012_ReadRegister(uint16_t command, TLE5012_Frame_t *frame)
{
    if (frame == NULL) {
        return HAL_ERROR;
    }

    /* 1. Select the sensor (>= tCSs = 105 ns before the first SCK edge) */
    CS_LOW();
    bit_delay();

    /* 2. Command phase - STM32 drives DATA, MSB first                  */
    DATA_DirOutput();
    WriteWord(command);

    /* 3. Turn the bus around: release DATA, wait >= twr_delay = 130 ns */
    DATA_DirInput();
    bit_delay();

    /* 4. Data phase - sensor drives DATA with the requested register   */
    frame->raw_data = ReadWord();

    /* 5. Safety word - sent automatically because ND >= 1 in command   */
    frame->safety_word = ReadWord();

    /* 6. Deselect (>= tCSh = 105 ns hold, then >= tCSoff = 600 ns idle) */
    bit_delay();
    CS_HIGH();
    bit_delay();
    bit_delay();

    /* Optional integrity check */
    uint8_t bytes[4] = {
        (uint8_t)(command >> 8), (uint8_t)command,
        (uint8_t)(frame->raw_data >> 8), (uint8_t)frame->raw_data
    };
    uint8_t expected_crc = TLE5012_CRC8(bytes, 4);
    frame->crc_ok = (expected_crc == (uint8_t)(frame->safety_word & 0xFFU)) ? 1U : 0U;

    return HAL_OK;
}

/* ---------------------------------------------------------------------
 * AVAL register: bit15 = status/roll bit, bits14..0 = two's-complement
 * signed angle, 15-bit resolution over 360 degrees (360/32768 ~ 0.011deg)
 * ------------------------------------------------------------------- */
float TLE5012_ConvertAngleDeg(uint16_t raw_data)
{
    int16_t signed_angle = (int16_t)(raw_data << 1);  /* drop status bit */
    signed_angle >>= 1;                                 /* sign-extend    */
    return ((float)signed_angle * 360.0f) / 32768.0f;
}

HAL_StatusTypeDef TLE5012_ReadAngleDeg(float *angle_deg, TLE5012_Frame_t *frame)
{
    TLE5012_Frame_t local;
    HAL_StatusTypeDef status = TLE5012_ReadRegister(TLE5012_CMD_READ_ANGLE, &local);
    if (status != HAL_OK) {
        return status;
    }
    if (angle_deg != NULL) {
        *angle_deg = TLE5012_ConvertAngleDeg(local.raw_data);
    }
    if (frame != NULL) {
        *frame = local;
    }
    return HAL_OK;
}
