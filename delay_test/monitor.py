"""Live: python monitor.py --port COM5
Offline: python monitor.py --csv capture.csv
Offline, drag a span containing ONE complete press and some idle time.
"""
import argparse
import csv
import json
import queue
import threading
from pathlib import Path
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import SpanSelector

FIELDS = 'seq,t_ms,status_t_ms,raw_pre,raw_post,baseline,diff,status,on_th,off_th,debounce'.split(',')

def crossing(t, y):
    hits = np.flatnonzero((y[:-1] <= 0) & (y[1:] > 0))
    if not len(hits):
        return None
    i = int(hits[0])
    return float(t[i] + (t[i+1]-t[i])*(-y[i])/(y[i+1]-y[i]))

def metrics(a):
    if len(a) < 5:
        raise ValueError('Select a wider interval.')
    t = a[:,1].astype(float)
    if np.any(np.diff(t) <= 0) or np.any(np.diff(a[:,0]) != 1):
        raise ValueError('Missing/reset/duplicate frames in selection: choose an intact interval.')
    dt = np.diff(t)
    if np.max(dt) > 2*np.median(dt):
        raise ValueError('Large sampling gap in selection; delay is unreliable.')
    state = a[:,7].astype(int)
    rises = np.flatnonzero((state[:-1] == 0) & (state[1:] == 1))+1
    falls = np.flatnonzero((state[:-1] == 1) & (state[1:] == 0))+1
    if state[0] != 0 or state[-1] != 0 or len(rises) != 1 or len(falls) != 1:
        raise ValueError('Select ONE complete 0->1->0 press with idle margins.')
    rise, fall = int(rises[0]), int(falls[0])
    pre = a[:,3]-a[:,5]
    post = a[:,4]-a[:,5]
    # Use exported algorithm diff for final signal threshold comparison.
    tc_pre = crossing(t[:rise+1], pre[:rise+1]-a[:rise+1,8])
    tc_post = crossing(t[:rise+1], a[:rise+1,6]-a[:rise+1,8])
    ts = float(a[rise,2])
    out = {'sampling_interval_median_ms':float(np.median(dt)),
           'sampling_interval_max_ms':float(np.max(dt)),
           'status_on_ms':ts, 'threshold_pre_cross_ms':tc_pre,
           'threshold_post_cross_ms':tc_post,
           'status_minus_pre_ms':None if tc_pre is None else ts-tc_pre,
           'status_minus_post_ms':None if tc_post is None else ts-tc_post}
    # First 20% of pre-Status region estimates idle; user must include idle margin.
    k = max(2, rise//5)
    if rise < 5:
        raise ValueError('Include more idle time before the press.')
    half_times = []
    for y in (a[:,3], a[:,4]):
        idle = float(np.median(y[:k]))
        peak = float(np.max(y[:fall+1]))
        noise = float(np.ptp(y[:k]))
        if peak-idle <= max(3*noise, 1):
            half_times.append(None)
        else:
            peak_i = int(np.argmax(y[:fall+1]))
            half_times.append(crossing(t[:peak_i+1], y[:peak_i+1]-(idle+peak)/2))
    out['half_pre_ms'], out['half_post_ms'] = half_times
    out['filter_half_delay_ms'] = None if None in half_times else half_times[1]-half_times[0]
    # Export peak lag only as supplementary, waveform-dependent measure.
    out['peak_delay_ms'] = float(t[np.argmax(a[:fall+1,4])]-t[np.argmax(a[:fall+1,3])])
    out['threshold_cross_lag_ms'] = None if tc_pre is None or tc_post is None else tc_post-tc_pre
    return out

def draw(ax, a, relative=False):
    for x in ax: x.clear()
    t = a[:,1]/1000
    if relative:
        ax[0].plot(t, a[:,3]-a[:,5], label='Pre-software filter - baseline')
        ax[0].plot(t, a[:,4]-a[:,5], label='Final filtered - baseline')
        ax[0].plot(t, a[:,8], '--', label='ON threshold')
        ax[0].plot(t, a[:,9], ':', label='OFF threshold')
        ax[0].set_ylabel('Difference (counts)')
    else:
        ax[0].plot(t, a[:,3], label='Pre-software filter (CIC2 included)')
        ax[0].plot(t, a[:,4], label='Final filtered')
        ax[0].set_ylabel('Rawcount')
    ax[1].step(a[:,2]/1000, a[:,7], where='post', label='Status')
    ax[1].set_ylim(-.1,1.2)
    ax[1].set_yticks([0,1])
    ax[1].set_ylabel('Status')
    ax[1].set_xlabel('Board time (s)')
    ax[0].legend(loc='upper left')
    for x in ax: x.grid(True, alpha=.3)

def offline(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        a = np.array([[int(r[k]) for k in FIELDS] for r in csv.DictReader(f)], dtype=np.int64)
    if len(a) < 5: raise ValueError('Not enough data.')
    fig, ax = plt.subplots(2,1,sharex=True,figsize=(12,7), gridspec_kw={'height_ratios':[3,1]})
    draw(ax,a,True)
    ax[0].set_title('Drag over ONE complete press + idle margins to measure')
    def selected(lo,hi):
        b=a[(a[:,1]/1000>=lo)&(a[:,1]/1000<=hi)]
        try: result=metrics(b)
        except ValueError as e:
            print('Selection rejected:',e); return
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        prefix=Path(path).with_name('event_'+stamp)
        with open(str(prefix)+'.json','w',encoding='utf-8') as f:
            json.dump(result,f,indent=2)
        print(json.dumps(result,indent=2))
        f2,axs=plt.subplots(2,1,sharex=True,figsize=(12,7), gridspec_kw={'height_ratios':[3,1]})
        draw(axs,b,True)
        for key,color,label in [('threshold_pre_cross_ms','C0','Pre crosses ON'),
                                ('threshold_post_cross_ms','C1','Final crosses ON'),
                                ('status_on_ms','C2','Status ON')]:
            if result[key] is not None:
                for x in axs: x.axvline(result[key]/1000,color=color,ls='--',alpha=.7)
        def fmt(v): return 'N/A' if v is None else f'{v:.2f} ms'
        axs[0].set_title('Filter half delay: '+fmt(result['filter_half_delay_ms'])+
                        ' | Status-pre: '+fmt(result['status_minus_pre_ms'])+
                        ' | Status-final: '+fmt(result['status_minus_post_ms']))
        f2.tight_layout(); f2.savefig(str(prefix)+'.png',dpi=180)
        plt.show(block=False)
    selector=SpanSelector(ax[0],selected,'horizontal',useblit=True,
                          props=dict(alpha=.2,facecolor='tab:blue'),interactive=True)
    fig.tight_layout(); plt.show()

def live(port, baud, output):
    import serial
    incoming=queue.Queue()
    stop=threading.Event()
    errors=[]
    def reader():
        try:
            with serial.Serial(port,baud,timeout=.2) as s, open(output,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.writer(f); w.writerow(FIELDS)
                while not stop.is_set():
                    line=s.readline().decode('ascii',errors='ignore').strip()
                    parts=line.split(',')
                    if len(parts)!=len(FIELDS): continue
                    try: row=[int(v) for v in parts]
                    except ValueError: continue
                    w.writerow(row); f.flush(); incoming.put(row)
        except Exception as e:
            errors.append(str(e)); stop.set()
    worker=threading.Thread(target=reader,daemon=True); worker.start()
    fig,ax=plt.subplots(2,1,sharex=True,figsize=(12,7),gridspec_kw={'height_ratios':[3,1]})
    history=[]
    def update(_):
        while True:
            try: history.append(incoming.get_nowait())
            except queue.Empty: break
        if history:
            history[:]=history[-2000:]
            a=np.array(history,dtype=np.int64)
            draw(ax,a)
            ax[0].set_xlim(max(a[0,1]/1000,a[-1,1]/1000-10),a[-1,1]/1000+.1)
            ax[0].set_title('100 Hz target; close window to stop and save')
        if errors:
            print('Serial error:',errors[0]); plt.close(fig)
    animation=FuncAnimation(fig,update,interval=100,cache_frame_data=False)
    fig.tight_layout()
    try: plt.show()
    finally:
        stop.set(); worker.join(timeout=2)
    print('Saved:',output)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--port'); g.add_argument('--csv')
    p.add_argument('--baud',type=int,default=115200)
    p.add_argument('--out',default='capture_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')
    args=p.parse_args()
    if args.csv: offline(args.csv)
    else: live(args.port,args.baud,args.out)
