"""UART acquisition companion for monitor_auto.py (Python 3.10+)."""
import csv
from collections import deque
import numpy as np
import matplotlib.pyplot as plt
FIELDS=['seq','t_scan_ms','t_process_ms','raw_pre','raw_post','baseline','diff',
        'status','th_on','th_off','frame_period_ms']

def polarity(a):
    delta=a[:,4].astype(float)-a[:,5]
    m=a[:,6]>5
    return -1 if np.any(m) and np.median(delta[m])<0 else 1

def draw(ax,a,thresholds=True):
    for x in ax:x.clear()
    t=a[:,1]/1000
    sign=polarity(a)
    ax[0].plot(t,sign*(a[:,3]-a[:,5]),label='Before software filter (signed raw-baseline)')
    ax[0].plot(t,sign*(a[:,4]-a[:,5]),label='After software filter (signed raw-baseline)')
    ax[0].plot(t,a[:,6],label='Library diff (clipped)',alpha=.7)
    if thresholds:
        ax[0].plot(t,a[:,8],'--',label='ON threshold')
        ax[0].plot(t,a[:,9],':',label='OFF threshold')
    ax[0].set_ylabel('Counts');ax[0].legend(fontsize=8);ax[0].grid(alpha=.3)
    ax[1].step(a[:,2]/1000,a[:,7],where='post',label='Status')
    ax[1].set_ylim(-.1,1.1);ax[1].set_ylabel('Status');ax[1].set_xlabel('Board time (s)');ax[1].grid(alpha=.3)

def live(port,baud,out):
    import serial
    import time
    q=deque(maxlen=1500)
    fig,ax=plt.subplots(2,1,sharex=True,figsize=(12,7))
    plt.ion();plt.show()
    print('Recording. Close plot or press Ctrl+C to finish and analyze.')
    print('Wait 3 seconds, then press/hold/release. Avoid resetting the board.')
    with serial.Serial(port,baud,timeout=.05) as ser, open(out,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(FIELDS);n=0;pending=b'';last_draw=time.monotonic()
        try:
            while plt.fignum_exists(fig.number):
                pending+=ser.read(ser.in_waiting or 1)
                lines=pending.split(b'\n');pending=lines.pop()
                for line in lines:
                    try: row=[int(x) for x in line.strip().split(b',')]
                    except ValueError: continue
                    if len(row)==len(FIELDS):
                        w.writerow(row);q.append(row);n+=1
                now=time.monotonic()
                if now-last_draw>=.25:
                    f.flush()
                    if q:
                        a=np.array(q,dtype=np.int64)
                        # Keep every row in CSV, draw up to the latest 10 seconds.
                        a=a[a[:,1]>=a[-1,1]-10000]
                        draw(ax,a);fig.canvas.draw_idle()
                        fig.suptitle(f'Frames saved: {n} | latest board time: {q[-1][1]/1000:.2f} s | Status: {q[-1][7]}')
                    last_draw=now
                    plt.pause(.001)
        except KeyboardInterrupt: pass
    plt.close(fig);plt.ioff();print(f'Saved {n} frames: {out}')
