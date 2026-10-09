/* CSX delay test: fixed Active mode, one actual scan/process per frame.
 * Configure SCB0 as UART named DELAY_UART, 115200 8N1, RX=P2.2 TX=P2.3.
 * Do not use EZI2C/Tuner in this measurement build.
 */
#include "cy_pdl.h"
#include "cybsp.h"
#include "cycfg.h"
#include "cycfg_capsense.h"
#include <stdio.h>
#define CHECK_OK(expr) do { if (!(expr)) { __disable_irq(); for (;;) { } } } while (0)
#define FRAME_PERIOD_MS 10u
#define WIDGET_ID CY_CAPSENSE_BUTTON0_WDGT_ID
/* Middleware raw usually has positive touch response. Python detects polarity
 * from raw_post versus library diff for each press; do not invert in firmware. */
static volatile uint32_t clock_ms;
static cy_stc_scb_uart_context_t uart_context;
static void tick(void) { ++clock_ms; }
static void capsense_isr(void)
{
    Cy_CapSense_InterruptHandler(CY_MSCLP0_HW, &cy_capsense_context);
}
int main(void)
{
    uint32_t seq=0u, next;
    char line[180];
    CHECK_OK(cybsp_init() == CY_RSLT_SUCCESS);
    const cy_stc_sysint_t irq = { .intrSrc=CY_MSCLP0_LP_IRQ, .intrPriority=3u };
    /* Millisecond board clock: remains running because no DeepSleep is used. */
    Cy_SysTick_Init(CY_SYSTICK_CLOCK_SOURCE_CLK_CPU, SystemCoreClock / 1000u);
    Cy_SysTick_SetCallback(0u, tick);
    __enable_irq();
    CHECK_OK(Cy_SCB_UART_Init(DELAY_UART_HW, &DELAY_UART_config,
                             &uart_context) == CY_SCB_UART_SUCCESS);
    Cy_SCB_UART_Enable(DELAY_UART_HW);
    CHECK_OK(Cy_CapSense_Init(&cy_capsense_context) == CY_CAPSENSE_STATUS_SUCCESS);
    Cy_SysInt_Init(&irq, capsense_isr);
    NVIC_ClearPendingIRQ(irq.intrSrc);
    NVIC_EnableIRQ(irq.intrSrc);
    CHECK_OK(Cy_CapSense_Enable(&cy_capsense_context) == CY_CAPSENSE_STATUS_SUCCESS);
    Cy_CapSense_IloCompensate(&cy_capsense_context);
    /* Minimize MSCLP inter-frame timer; software below schedules actual frames. */
    Cy_CapSense_ConfigureMsclpTimer(1u, &cy_capsense_context);
    Cy_SCB_UART_PutString(DELAY_UART_HW,
        "seq,t_scan_ms,t_process_ms,raw_pre,raw_post,baseline,diff,status,th_on,th_off,frame_period_ms\r\n");
    next=clock_ms;
    for (;;)
    {
        while ((int32_t)(clock_ms-next)<0) { }
        cy_stc_capsense_sensor_context_t *s =
            cy_capsense_context.ptrWdConfig[WIDGET_ID].ptrSnsContext;
        cy_stc_capsense_widget_context_t *w =
            cy_capsense_context.ptrWdConfig[WIDGET_ID].ptrWdContext;
        CHECK_OK(Cy_CapSense_ScanAllSlots(&cy_capsense_context) == CY_CAPSENSE_STATUS_SUCCESS);
        while (Cy_CapSense_IsBusy(&cy_capsense_context)) { }
        uint32_t ts=clock_ms;
        /* CSX processing limits then inverts scan raw BEFORE software IIR.
         * Convert only the exported copy; leave s->raw untouched so the library
         * performs its own preprocessing exactly once. No hard-coded maximum. */
        uint32_t scan_raw=s->raw;
        uint32_t max_raw=w->maxRawCount;
        uint32_t pre=max_raw-(scan_raw>max_raw?max_raw:scan_raw);
        Cy_CapSense_ProcessAllWidgets(&cy_capsense_context);
        uint32_t tp=clock_ms;
        uint32_t active=Cy_CapSense_IsWidgetActive(WIDGET_ID,&cy_capsense_context)?1u:0u;
        Cy_GPIO_Write(CYBSP_USER_LED2_PORT,CYBSP_USER_LED2_NUM,active);
        /* ON threshold = Finger + Hysteresis; OFF = Finger - Hysteresis. */
        uint32_t on=(uint32_t)w->fingerTh+w->hysteresis;
        uint32_t off=w->fingerTh>w->hysteresis?w->fingerTh-w->hysteresis:0u;
        snprintf(line,sizeof(line),"%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu\r\n",
            (unsigned long)seq++, (unsigned long)ts,(unsigned long)tp,
            (unsigned long)pre,(unsigned long)s->raw,(unsigned long)s->bsln,
            (unsigned long)s->diff,(unsigned long)active,(unsigned long)on,
            (unsigned long)off,(unsigned long)FRAME_PERIOD_MS);
        Cy_SCB_UART_PutString(DELAY_UART_HW,line);
        while (!Cy_SCB_UART_IsTxComplete(DELAY_UART_HW)) { }
        next+=FRAME_PERIOD_MS;
        /* Never produce catch-up bursts after an overrun. CSV records real time. */
        if ((int32_t)(clock_ms-next)>0) next=clock_ms;
    }
}
