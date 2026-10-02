/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "lwip.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

#include "udp_server.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
ADC_HandleTypeDef hadc1;

TIM_HandleTypeDef htim2;
TIM_HandleTypeDef htim3;

UART_HandleTypeDef huart3;

PCD_HandleTypeDef hpcd_USB_OTG_FS;

/* USER CODE BEGIN PV */

volatile int32_t encoder_position = 0; //right stick
static uint32_t last_tick_ms = 0;
static uint16_t last_timer = 0;

volatile int32_t encoder2_position = 0;     // left stick (TIM4)
static uint16_t last_timer2 = 0;

volatile int32_t accelerator_position = 0;
volatile int32_t brake_position = 0;
volatile int32_t clutch_position = 0;

static uint32_t accel_filtered = 0;
static uint32_t brake_filtered = 0;
static uint32_t clutch_filtered = 0;

static uint8_t accel_filter_init = 0;
static uint8_t brake_filter_init = 0;
static uint8_t clutch_filter_init = 0;

/* Adjust these raw ADC values to your actual hardware pedal endpoints */
#define ACCEL_ADC_MIN   3740
#define ACCEL_ADC_MAX   260

#define BRAKE_ADC_MIN   3720
#define BRAKE_ADC_MAX   1150

#define CLUTCH_ADC_MIN  3720
#define CLUTCH_ADC_MAX  300

/* Tiller calibration — placeholder values, replace after calibration */
#define RIGHT_STICK_MIN   0
#define RIGHT_STICK_MAX   480

#define LEFT_STICK_MIN    0
#define LEFT_STICK_MAX    -480

/* Final scaled values sent over UDP */
volatile float right_stick_scaled = 0.0f;
volatile float left_stick_scaled  = 0.0f;
volatile float accelerator_scaled = 0.0f;
volatile float brake_scaled = 0.0f;
volatile float clutch_scaled = 0.0f;

volatile int32_t gearbox_x = 0;   // back pot — left/right column
volatile int32_t gearbox_y = 0;   // side pot — front/back row

static uint32_t x_filtered = 0;
static uint32_t y_filtered = 0;
static uint8_t x_filter_init = 0;
static uint8_t y_filter_init = 0;

volatile int32_t current_gear = 0;   // final gear output: -1=R, 0=N, 1-5=gears

/* Calibration placeholders — measured raw values at each gear position */
#define X_LEFT_RAW     1100     // X raw value (back pot) when stick is at LEFT column (3/2)
#define X_CENTER_RAW   1825    // X raw value (back pot) when stick is at CENTER column (R/1)
#define X_RIGHT_RAW    2890    // X raw value (back pot) when stick is at RIGHT column (4/5)

#define Y_TOP_RAW      3525    // Y raw value (side pot) when stick is pulled BACK (3/R/4)
#define Y_NEUTRAL_RAW  1980    // Y raw value (side pot) when stick is at NEUTRAL row
#define Y_BOTTOM_RAW   440    // Y raw value (side pot) when stick is pushed FORWARD (2/1/5)

#define ZONE_TOLERANCE    400     // +/- range around each raw value to still count as "in zone"

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MPU_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_USB_OTG_FS_PCD_Init(void);
static void MX_TIM3_Init(void);
static void MX_ADC1_Init(void);
static void MX_TIM2_Init(void);
/* USER CODE BEGIN PFP */
static float scale_to_0_1(int32_t raw, int32_t min_val, int32_t max_val);
static float read_adc_pedal_0_1(uint32_t channel, uint32_t *filtered, uint8_t *filter_init,
                                 int32_t min_val, int32_t max_val, int32_t *raw_out);
static int32_t compute_gear(int32_t side_raw, int32_t back_raw);
static int32_t read_adc_raw_filtered(uint32_t channel, uint32_t *filtered, uint8_t *filter_init);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/**
  * @brief  Reads ADC channel, applies moving average filter, and returns scaled float [0.0, 1.0].
  */
static float read_adc_pedal_0_1(uint32_t channel, uint32_t *filtered, uint8_t *filter_init,
                                 int32_t min_val, int32_t max_val, int32_t *raw_out)
{
    ADC_ChannelConfTypeDef sConfig = {0};
    sConfig.Channel = channel;
    sConfig.Rank = ADC_REGULAR_RANK_1;
    sConfig.SamplingTime = ADC_SAMPLETIME_56CYCLES;
    HAL_ADC_ConfigChannel(&hadc1, &sConfig);

    HAL_ADC_Start(&hadc1);
    HAL_ADC_PollForConversion(&hadc1, 10);
    uint32_t raw = HAL_ADC_GetValue(&hadc1);
    HAL_ADC_Stop(&hadc1);

    if (!(*filter_init)) {
        *filtered = raw;
        *filter_init = 1;
    } else {
        *filtered = *filtered + ((int32_t)raw - (int32_t)(*filtered)) / 3;
    }

    int32_t clamped = (int32_t)(*filtered);
    *raw_out = clamped;

    if (max_val == min_val) return 0.0f;

    float norm;
    // Handle inverted ADC polarity (when fully pressed yields a lower raw value)
    if (min_val > max_val) {
        if (clamped > min_val) clamped = min_val;
        if (clamped < max_val) clamped = max_val;
        norm = (float)(min_val - clamped) / (float)(min_val - max_val);
    } else {
        if (clamped < min_val) clamped = min_val;
        if (clamped > max_val) clamped = max_val;
        norm = (float)(clamped - min_val) / (float)(max_val - min_val);
    }

    if (norm < 0.0f) norm = 0.0f;
    if (norm > 1.0f) norm = 1.0f;

    return norm;
}

/**
  * @brief Scales input integer to [-1.0, 1.0] range (used for sticks/encoders).
  */
static float scale_to_0_1(int32_t raw, int32_t min_val, int32_t max_val)
{
    if (max_val == min_val) return 0.0f; // Prevent divide by zero

    int32_t clamped = raw;
    float norm;

    if (min_val <= max_val) {
        if (clamped < min_val) clamped = min_val;
        if (clamped > max_val) clamped = max_val;
        norm = (float)(clamped - min_val) / (float)(max_val - min_val);
    } else {
        // Inverted range (e.g. LEFT_STICK_MAX < LEFT_STICK_MIN)
        if (clamped > min_val) clamped = min_val;
        if (clamped < max_val) clamped = max_val;
        norm = (float)(min_val - clamped) / (float)(min_val - max_val);
    }

    if (norm < 0.0f) norm = 0.0f;
    if (norm > 1.0f) norm = 1.0f;

    return norm;
}

static int32_t compute_gear(int32_t x_raw, int32_t y_raw)
{
    int col;
    if (abs(x_raw - X_LEFT_RAW) < ZONE_TOLERANCE) col = -1;
    else if (abs(x_raw - X_CENTER_RAW) < ZONE_TOLERANCE) col = 0;
    else if (abs(x_raw - X_RIGHT_RAW) < ZONE_TOLERANCE) col = 1;
    else col = 0;

    int row;
    if (abs(y_raw - Y_TOP_RAW) < ZONE_TOLERANCE) row = -1;
    else if (abs(y_raw - Y_NEUTRAL_RAW) < ZONE_TOLERANCE) row = 0;
    else if (abs(y_raw - Y_BOTTOM_RAW) < ZONE_TOLERANCE) row = 1;
    else row = 0;

    if (row == 0) return 0;

    if (row == -1) {
        if (col == -1) return 3;
        if (col == 0)  return 6;
        if (col == 1)  return 4;
    }

    if (row == 1) {
        if (col == -1) return 2;
        if (col == 0)  return 1;
        if (col == 1)  return 5;
    }

    return 0;
}

static int32_t read_adc_raw_filtered(uint32_t channel, uint32_t *filtered, uint8_t *filter_init)
{
    ADC_ChannelConfTypeDef sConfig = {0};
    sConfig.Channel = channel;
    sConfig.Rank = 1;
    sConfig.SamplingTime = ADC_SAMPLETIME_56CYCLES;
    HAL_ADC_ConfigChannel(&hadc1, &sConfig);

    HAL_ADC_Start(&hadc1);
    HAL_ADC_PollForConversion(&hadc1, 10);
    uint32_t raw = HAL_ADC_GetValue(&hadc1);
    HAL_ADC_Stop(&hadc1);

    if (!(*filter_init)) {
        *filtered = raw;
        *filter_init = 1;
    } else {
        *filtered = *filtered + ((int32_t)raw - (int32_t)(*filtered)) / 3;
    }

    return (int32_t)(*filtered);
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MPU Configuration--------------------------------------------------------*/
  MPU_Config();

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART3_UART_Init();
  MX_USB_OTG_FS_PCD_Init();
  MX_TIM3_Init();
  MX_LWIP_Init();
  MX_ADC1_Init();
  MX_TIM2_Init();
  /* USER CODE BEGIN 2 */
  HAL_Delay(500);
  HAL_TIM_Encoder_Start(&htim3, TIM_CHANNEL_ALL); // right
  HAL_TIM_Encoder_Start(&htim2, TIM_CHANNEL_ALL); // left
  last_timer = (uint16_t)__HAL_TIM_GET_COUNTER(&htim3);
  last_timer2 = (uint16_t)__HAL_TIM_GET_COUNTER(&htim2);
  last_tick_ms = HAL_GetTick();
  udp_server_init();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
      MX_LWIP_Process();

      uint32_t now = HAL_GetTick();
      if ((now - last_tick_ms) >= 10)
      {
          uint16_t current_timer = (uint16_t)__HAL_TIM_GET_COUNTER(&htim3);
          int16_t delta = (int16_t)(current_timer - last_timer);
          encoder_position += (int32_t)delta;
          last_timer = current_timer;

          uint16_t current_timer2 = (uint16_t)__HAL_TIM_GET_COUNTER(&htim2);
          int16_t delta2 = (int16_t)(current_timer2 - last_timer2);
          encoder2_position += (int32_t)delta2;
          last_timer2 = current_timer2;

          last_tick_ms = now;

          /* Read Pedals (Outputs [0.0, 1.0] float directly) */
          /* Note: ADC_CHANNEL_8 corresponds to PB0 pin for Brake */
          accelerator_scaled = read_adc_pedal_0_1(ADC_CHANNEL_0, &accel_filtered, &accel_filter_init,
                                                  ACCEL_ADC_MIN, ACCEL_ADC_MAX, (int32_t*)&accelerator_position);

          brake_scaled       = read_adc_pedal_0_1(ADC_CHANNEL_8, &brake_filtered, &brake_filter_init,
                                                  BRAKE_ADC_MIN, BRAKE_ADC_MAX, (int32_t*)&brake_position);

          clutch_scaled      = read_adc_pedal_0_1(ADC_CHANNEL_4, &clutch_filtered, &clutch_filter_init,
                                                  CLUTCH_ADC_MIN, CLUTCH_ADC_MAX, (int32_t*)&clutch_position);

          /* Read Sticks (Outputs [-1.0, 1.0] float) */
          right_stick_scaled = scale_to_0_1(encoder_position, RIGHT_STICK_MIN, RIGHT_STICK_MAX);
          left_stick_scaled  = scale_to_0_1(encoder2_position, LEFT_STICK_MIN, LEFT_STICK_MAX);

          gearbox_x = read_adc_raw_filtered(ADC_CHANNEL_13, &x_filtered, &x_filter_init);   // back pot → X
          gearbox_y = read_adc_raw_filtered(ADC_CHANNEL_12, &y_filtered, &y_filter_init);   // side pot → Y

          current_gear = compute_gear(gearbox_x, gearbox_y);

          udp_server_send_position();

          char dbg3[176];
                    int dbg3_len = snprintf(dbg3, sizeof(dbg3),
                        "Acc:%.2f (Raw:%ld) Brk:%.2f (Raw:%ld) Clt:%.2f (Raw:%ld) "
                        "X:%ld Y:%ld Gear:%ld RT:%.2f (Raw:%ld) LT:%.2f (Raw:%ld)\r\n",
                        accelerator_scaled, accelerator_position,
                        brake_scaled, brake_position,
                        clutch_scaled, clutch_position,
                        gearbox_x, gearbox_y, current_gear,
                        right_stick_scaled, encoder_position,
                        left_stick_scaled, encoder2_position);
                    HAL_UART_Transmit(&huart3, (uint8_t*)dbg3, dbg3_len, 100);
      }
  /* USER CODE END 3 */
}
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure LSE Drive Capability
  */
  HAL_PWR_EnableBkUpAccess();

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_BYPASS;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 4;
  RCC_OscInitStruct.PLL.PLLN = 216;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 9;
  RCC_OscInitStruct.PLL.PLLR = 2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Activate the Over-Drive mode
  */
  if (HAL_PWREx_EnableOverDrive() != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_7) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Configure the global features of the ADC (Clock, Resolution, Data Alignment and number of conversion)
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
  hadc1.Init.Resolution = ADC_RESOLUTION_12B;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
  hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.NbrOfConversion = 1;
  hadc1.Init.DMAContinuousRequests = DISABLE;
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure for the selected ADC regular channel its corresponding rank in the sequencer and its sample time.
  */
  sConfig.Channel = ADC_CHANNEL_0;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_3CYCLES;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{

  /* USER CODE BEGIN TIM2_Init 0 */

  /* USER CODE END TIM2_Init 0 */

  TIM_Encoder_InitTypeDef sConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM2_Init 1 */

  /* USER CODE END TIM2_Init 1 */
  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 0;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 65535;
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  sConfig.EncoderMode = TIM_ENCODERMODE_TI12;
  sConfig.IC1Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC1Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC1Prescaler = TIM_ICPSC_DIV1;
  sConfig.IC1Filter = 0;
  sConfig.IC2Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC2Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC2Prescaler = TIM_ICPSC_DIV1;
  sConfig.IC2Filter = 0;
  if (HAL_TIM_Encoder_Init(&htim2, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM2_Init 2 */

  /* USER CODE END TIM2_Init 2 */

}

/**
  * @brief TIM3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM3_Init(void)
{

  /* USER CODE BEGIN TIM3_Init 0 */

  /* USER CODE END TIM3_Init 0 */

  TIM_Encoder_InitTypeDef sConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM3_Init 1 */

  /* USER CODE END TIM3_Init 1 */
  htim3.Instance = TIM3;
  htim3.Init.Prescaler = 0;
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = 65535;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  sConfig.EncoderMode = TIM_ENCODERMODE_TI12;
  sConfig.IC1Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC1Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC1Prescaler = TIM_ICPSC_DIV1;
  sConfig.IC1Filter = 0;
  sConfig.IC2Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC2Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC2Prescaler = TIM_ICPSC_DIV1;
  sConfig.IC2Filter = 0;
  if (HAL_TIM_Encoder_Init(&htim3, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM3_Init 2 */

  /* USER CODE END TIM3_Init 2 */

}

/**
  * @brief USART3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART3_UART_Init(void)
{

  /* USER CODE BEGIN USART3_Init 0 */

  /* USER CODE END USART3_Init 0 */

  /* USER CODE BEGIN USART3_Init 1 */

  /* USER CODE END USART3_Init 1 */
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART3_Init 2 */

  /* USER CODE END USART3_Init 2 */

}

/**
  * @brief USB_OTG_FS Initialization Function
  * @param None
  * @retval None
  */
static void MX_USB_OTG_FS_PCD_Init(void)
{

  /* USER CODE BEGIN USB_OTG_FS_Init 0 */

  /* USER CODE END USB_OTG_FS_Init 0 */

  /* USER CODE BEGIN USB_OTG_FS_Init 1 */

  /* USER CODE END USB_OTG_FS_Init 1 */
  hpcd_USB_OTG_FS.Instance = USB_OTG_FS;
  hpcd_USB_OTG_FS.Init.dev_endpoints = 6;
  hpcd_USB_OTG_FS.Init.speed = PCD_SPEED_FULL;
  hpcd_USB_OTG_FS.Init.dma_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.phy_itface = PCD_PHY_EMBEDDED;
  hpcd_USB_OTG_FS.Init.Sof_enable = ENABLE;
  hpcd_USB_OTG_FS.Init.low_power_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.lpm_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.vbus_sensing_enable = ENABLE;
  hpcd_USB_OTG_FS.Init.use_dedicated_ep1 = DISABLE;
  if (HAL_PCD_Init(&hpcd_USB_OTG_FS) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USB_OTG_FS_Init 2 */

  /* USER CODE END USB_OTG_FS_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  /* Enable SYSCFG Clock to allow pin remapping/debug release */
  __HAL_RCC_SYSCFG_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, LD3_Pin|LD2_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(USB_PowerSwitchOn_GPIO_Port, USB_PowerSwitchOn_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : USER_Btn_Pin */
  GPIO_InitStruct.Pin = USER_Btn_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(USER_Btn_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : LD3_Pin LD2_Pin */
  GPIO_InitStruct.Pin = LD3_Pin|LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pin : USB_PowerSwitchOn_Pin */
  GPIO_InitStruct.Pin = USB_PowerSwitchOn_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(USB_PowerSwitchOn_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : USB_OverCurrent_Pin */
  GPIO_InitStruct.Pin = USB_OverCurrent_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(USB_OverCurrent_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* Analog Inputs for Pedals */
  GPIO_InitStruct.Pin = GPIO_PIN_4;
  GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = GPIO_PIN_0;
  GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE END MX_GPIO_Init_2 */
}
/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

 /* MPU Configuration */

void MPU_Config(void)
{
  MPU_Region_InitTypeDef MPU_InitStruct = {0};

  /* Disables the MPU */
  HAL_MPU_Disable();

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Enable = MPU_REGION_ENABLE;
  MPU_InitStruct.Number = MPU_REGION_NUMBER0;
  MPU_InitStruct.BaseAddress = 0x0;
  MPU_InitStruct.Size = MPU_REGION_SIZE_4GB;
  MPU_InitStruct.SubRegionDisable = 0x87;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.AccessPermission = MPU_REGION_NO_ACCESS;
  MPU_InitStruct.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  MPU_InitStruct.IsShareable = MPU_ACCESS_SHAREABLE;
  MPU_InitStruct.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  MPU_InitStruct.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);
  /* Enables the MPU */
  HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);

}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
