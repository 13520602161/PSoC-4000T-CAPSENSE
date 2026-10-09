# PSoC 4000T 实时力曲线使用方法

## 先验证电脑绘图

安装依赖：

```bash
python -m pip install matplotlib pyserial
```

将 `live_capsense_force.py` 与原始 Tuner CSV 放在同一目录，运行：

```bash
python live_capsense_force.py --demo capsense-tuner-20260928_170129-17528.csv
```

它按照原来的时间间隔回放采样，用来检查绘图界面。按窗口右上角的关闭按钮退出；当前目录会有 `live_force_日期_时间.csv`。

## 让开发板实时发送数据

此前烧录的程序只通过 I²C 与 Tuner 通信；单独运行 Python 不会让板子自动产生串口数据。请先备份工程，并在 ModusToolbox 的 Device Configurator 中把连接 KitProg3 的 UART 配置为 **115200, 8N1**，生成配置并初始化 UART。开发板 SW2 拨到 **UART**，重新编译和烧录。原 I²C Tuner 和这个 UART 方案使用 SW2 不同档位，切换后不要期望原 Tuner 继续用原 I²C 连接。

在固件每次完成 `Cy_CapSense_ProcessAllWidgets(&cy_capsense_context)` 且本帧扫描包括 **Button0** 之后、开始下一帧扫描之前加入下列核心逻辑。这里的 `YOUR_UART_HW` 请替换成工程 Device Configurator 实际生成的 UART `*_HW` 宏；UART 初始化代码需以你的工程生成为准。

```c
#include <stdio.h>
#include "cycfg_capsense.h"
#include "cy_pdl.h"

/* 程序中留在 main() 作用域或文件作用域均可 */
char force_line[32];

/* 放在 ProcessAllWidgets 成功之后；确认当前帧确实扫描了 Button0 */
const cy_stc_capsense_sensor_context_t *sensor =
    &cy_capsense_context.ptrWdConfig[CY_CAPSENSE_BUTTON0_WDGT_ID].ptrSnsContext[0u];
(void)snprintf(force_line, sizeof(force_line), "%u,%u\r\n",
               (unsigned)sensor->diff,
               (unsigned)(sensor->status & CY_CAPSENSE_SNS_TOUCH_STATUS_MASK ? 1u : 0u));
Cy_SCB_UART_PutString(YOUR_UART_HW, force_line);
```

保留工程原有的 CAPSENSE 扫描、状态处理和低功耗切换流程。WoT 阶段不应重复发送上一次的 Button0 数据；唤醒进入 Active 后再发送新的 Button0 扫描结果。若你希望待机时曲线回到零，可在进入 WoT 时另发一次 `0,0\r\n`。

在 Windows 的设备管理器找到 **KitProg3 USB UART 对应的 COM 口**。先关闭占用该串口的终端软件，执行（COM5 仅作示例）：

```bash
python live_capsense_force.py --port COM5 --baud 115200
```

曲线实时更新，窗口关闭后输出 CSV。当前映射是 **5703 个 DiffCount = 100 N** 的假设，超出 5703 会在界面提示 `OVER RANGE` 并将显示值限制为 100 N；真实力值须通过测力机标定。换用其他标定峰值可传入 `--peak-count 数值 --peak-force 数值`。

此方案图中横轴采用**电脑接收数据的时间**。请将板子持续保持相同 Active 扫描配置；若日后需要准确同步测力机时间戳，可以升级串口协议，发送板载时间戳。
