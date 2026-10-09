/* Delay experiment: fixed active scans, no Tuner, no deep sleep.
 * Configure SCB0 as UART named DELAY_UART (115200 8N1).
 * RX=P2.2, TX=P2.3. Keep CAPSENSE settings unchanged.
 */
#include "cy_pdl.h"
#include "cybsp.h"
#include "cycfg.h"
#include "cycfg_capsense.h"
#include <stdio.h>
#include <stdint.h>

#define PERIOD_MS (10u) /* 100 Hz experiment, not the original 128 Hz */
#ifndef DELAY_UART_HW
#error Configure UART named DELAY_UART in Device Configurator and save.
#endif
static volatile uint32_t clock_ms;
static cy_stc_scb_uart_context_t uart_context;
static void tick(void) { clock_ms++; }
static void capsense_isr(void)
{
    Cy_CapSense_InterruptHandler(CY_MSCLP0_HW, &cy_capsense_context);
}
static void stop_on_error(void) { __disable_irq(); for (;;) {} }
int main(void)
{
    if (cybsp_init() != CY_RSLT_SUCCESS) stop_on_error();
    if (Cy_SCB_UART_Init(DELAY_UART_HW, &DELAY_UART_config,
                        &uart_context) != CY_SCB_UART_SUCCESS) stop_on_error();
    Cy_SCB_UART_Enable(DELAY_UART_HW);
    const cy_stc_sysint_t irq = {
        .intrSrc = CY_MSCLP0_LP_IRQ, .intrPriority = 3u
    };
    if (Cy_CapSense_Init(&cy_capsense_context) != CY_CAPSENSE_STATUS_SUCCESS)
        stop_on_error();
    Cy_SysInt_Init(&irq, capsense_isr);
    NVIC_ClearPendingIRQ(irq.intrSrc);
    NVIC_EnableIRQ(irq.intrSrc);
    __enable_irq();
    if (Cy_CapSense_Enable(&cy_capsense_context) != CY_CAPSENSE_STATUS_SUCCESS)
        stop_on_error();
    Cy_CapSense_IloCompensate(&cy_capsense_context);
    /* Short internal wake timer; main loop defines the experiment cadence. */
    Cy_CapSense_ConfigureMsclpTimer(25u, &cy_capsense_context);
    SystemCoreClockUpdate();
    Cy_SysTick_Init(CY_SYSTICK_CLOCK_SOURCE_CLK_CPU, SystemCoreClock / 1000u - 1u);
    Cy_SysTick_SetCallback(0u, tick);
    cy_stc_capsense_sensor_context_t *sns =
        cy_capsense_context.ptrWdConfig[CY_CAPSENSE_BUTTON0_WDGT_ID].ptrSnsContext;
    cy_stc_capsense_widget_context_t *wd =
        cy_capsense_context.ptrWdConfig[CY_CAPSENSE_BUTTON0_WDGT_ID].ptrWdContext;
    char line[160];
    uint32_t seq = 0u, next = clock_ms + PERIOD_MS;
    Cy_SCB_UART_PutString(DELAY_UART_HW,
        "seq,t_ms,status_t_ms,raw_pre,raw_post,baseline,diff,status,on_th,off_th,debounce\r\n");
    for (;;)
    {
        while ((int32_t)(clock_ms - next) < 0) {} /* Stay awake: SysTick runs. */
        next += PERIOD_MS;
        if (Cy_CapSense_ScanAllSlots(&cy_capsense_context) != CY_CAPSENSE_STATUS_SUCCESS)
            stop_on_error();
        while (Cy_CapSense_IsBusy(&cy_capsense_context)) {}
        const uint32_t t = clock_ms;
        const uint16_t pre = sns->raw;
        Cy_CapSense_ProcessAllWidgets(&cy_capsense_context);
        const uint32_t ts = clock_ms;
        const uint16_t post = sns->raw;
        const uint16_t base = sns->bsln;
        const uint16_t diff = sns->diff;
        const unsigned active = Cy_CapSense_IsWidgetActive(
            CY_CAPSENSE_BUTTON0_WDGT_ID, &cy_capsense_context) ? 1u : 0u;
        const unsigned on = (unsigned)wd->fingerTh + wd->hysteresis;
        const unsigned off = wd->fingerTh > wd->hysteresis ?
                             wd->fingerTh - wd->hysteresis : 0u;
        const int n = snprintf(line, sizeof line,
            "%lu,%lu,%lu,%u,%u,%u,%u,%u,%u,%u,%u\r\n",
            (unsigned long)seq++, (unsigned long)t, (unsigned long)ts,
            (unsigned)pre, (unsigned)post, (unsigned)base, (unsigned)diff,
            active, on, off, (unsigned)wd->onDebounce);
        if (n > 0 && n < (int)sizeof line)
            Cy_SCB_UART_PutArrayBlocking(DELAY_UART_HW, line, (uint32_t)n);
        /* If overloaded, do not issue a burst of catch-up scans. */
        if ((int32_t)(clock_ms - next) >= 0) next = clock_ms + PERIOD_MS;
    }
}
