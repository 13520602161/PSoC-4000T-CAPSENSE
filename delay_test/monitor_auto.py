"""Automatic rise/fall analysis of CSV from the existing firmware.
python monitor_auto.py --port COM5 --out delay_iir64.csv --count 10
python monitor_auto.py --csv delay_iir64.csv --count 10
"""
import argparse, csv, json
from pathlib import Path
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from monitor import live, FIELDS, draw

DELAY_KEYS = [f'{edge}_{name}_ms' for edge in ('rise','fall')
              for name in ('filter_half_delay','status_minus_pre','status_minus_post')]

def crossings(t, y, rising=True):
    mask = ((y[:-1] <= 0)&(y[1:] > 0)) if rising else ((y[:-1] >= 0)&(y[1:] < 0))
    ids=np.flatnonzero(mask)
    return [float(t[i]+(t[i+1]-t[i])*(-y[i])/(y[i+1]-y[i])) for i in ids]

def prior_cross(t,y,lo,hi,rising):
    sel=(t>=lo)&(t<=hi)
    c=crossings(t[sel],y[sel],rising)
    # Last crossing before Status avoids assigning an earlier noise excursion.
    return (c[-1] if c else None),len(c)

def pair_events(a,gap_ms):
    state=a[:,7].astype(int); runs=[]; start=None
    for i,v in enumerate(state):
        if v and (i==0 or not state[i-1]): start=i
        if not v and i>0 and state[i-1]:
            runs.append([start,i,1]); start=None
    if start is not None: runs.append([start,None,1])
    groups=[]
    for r in runs:
        if groups and groups[-1][1] is not None and a[r[0],1]-a[groups[-1][1],1]<gap_ms:
            groups[-1][1]=r[1];groups[-1][2]+=1
        else: groups.append(r.copy())
    return groups

def analyze_event(a,start,end,rise,fall,toggles,settle_ms):
    out={'start_ms':float(a[start,1]),'end_ms':float(a[end-1,1]),
         'status_on_ms':float(a[rise,2]),'status_off_ms':float(a[fall,2]),
         'status_on_runs':toggles,'valid':False,'reason':''}
    out.update({k:None for k in DELAY_KEYS})
    b=a[start:end];t=b[:,1].astype(float); r=rise-start;f=fall-start
    def reject(reason): out['reason']=reason;return out
    if toggles!=1: return reject('Status chatter or merged presses; repeat with longer idle gap')
    if b[0,7] or b[-1,7]: return reject('Incomplete press or missing idle margin')
    if len(t)<6 or np.any(np.diff(t)<=0) or np.any(np.diff(b[:,0])!=1):
        return reject('Missing frames or board reset')
    dt=np.diff(t);out['sample_median_ms']=float(np.median(dt));out['sample_max_ms']=float(np.max(dt))
    if dt.max()>max(2.5*np.median(dt),30): return reject('Sampling gap')
    if t[r]-t[0]<settle_ms or t[-1]-t[f]<settle_ms:
        return reject('Insufficient idle margin')
    idle_len=max(3,int(np.ceil(settle_ms/np.median(dt))))
    if idle_len>=r: return reject('No reliable idle reference')
    pre=b[:,3]-b[:,5];post=b[:,6].astype(float)
    ton=float(a[rise,2]);toff=float(a[fall,2])
    for direction,lo,hi,threshold,ts in [('rise',t[0],t[r],b[:,8],ton),
                                       ('fall',t[r],t[f],b[:,9],toff)]:
        isrise=direction=='rise'
        for name,y in [('pre',pre),('post',post)]:
            tc,n=prior_cross(t,y-threshold,lo,hi,isrise)
            out[f'{direction}_{name}_cross_ms']=tc
            out[f'{direction}_{name}_cross_count']=n
            out[f'{direction}_status_minus_{name}_ms']=None if tc is None else ts-tc
    # Half-level waveform delay uses each signal's local baseline and peak.
    halves={}
    for name,col in [('pre',3),('post',4)]:
        y=b[:,col].astype(float);base=float(np.median(y[:idle_len]))
        peak_i=int(np.argmax(y[:f+1]));peak=float(y[peak_i]);noise=float(np.ptp(y[:idle_len]))
        out[f'{name}_amplitude']=peak-base
        if peak-base<=max(5*noise,5): return reject('Weak signal relative to idle noise')
        half=base+(peak-base)/2
        up=crossings(t[:peak_i+1],y[:peak_i+1]-half,True)
        down=crossings(t[peak_i:],y[peak_i:]-half,False)
        # Multiple half crossings cannot be silently assigned to a single press.
        if len(up)!=1 or len(down)!=1: return reject('Multiple/missing half crossings or multi-peak press')
        halves[name]=(up[0],down[0]);out[f'rise_{name}_half_ms']=up[0];out[f'fall_{name}_half_ms']=down[0]
        tail=float(np.median(y[-idle_len:]))
        if abs(tail-base)>max(.15*(peak-base),5*noise,5):
            return reject('Signal has not recovered or baseline drift is too large')
    out['rise_filter_half_delay_ms']=halves['post'][0]-halves['pre'][0]
    out['fall_filter_half_delay_ms']=halves['post'][1]-halves['pre'][1]
    if any(out[k] is None for k in DELAY_KEYS): return reject('Missing threshold crossing; check waveform/threshold')
    if out['rise_filter_half_delay_ms']< -np.median(dt) or out['fall_filter_half_delay_ms']< -np.median(dt):
        return reject('Filtered half crossing leads input unexpectedly')
    out['valid']=True
    out['reason']='Multiple threshold crossings: last preceding crossing used' if any(
        out.get(f'{d}_{n}_cross_count',0)>1 for d in ('rise','fall') for n in ('pre','post')) else 'OK'
    return out

def analyze_file(path,count=10,margin_ms=800,gap_ms=250,settle_ms=150):
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not set(FIELDS)<=set(reader.fieldnames or []): raise ValueError('CSV needs the original 11 firmware columns')
        a=np.array([[int(row[k]) for k in FIELDS] for row in reader],dtype=np.int64)
    if len(a)<10: raise ValueError('Not enough CSV data')
    # Refuse to combine resets/time-wraps into one experiment.
    if np.any(np.diff(a[:,1])<=0) or np.any(np.diff(a[:,0])<=0):
        raise ValueError('Board reset/time-wrap/duplicate frames: split the recording and retry')
    folder=Path(path).with_suffix('').with_name(Path(path).stem+'_analysis_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True)
    groups=pair_events(a,gap_ms);results=[];accepted=0
    for idx,(rise,fall,toggles) in enumerate(groups):
        if fall is None or rise==0:
            results.append({'event':idx+1,'valid':False,'used':False,'reason':'Incomplete Status event'});continue
        prev_end=groups[idx-1][1] if idx>0 else 0
        if prev_end is None: prev_end=0
        next_start=groups[idx+1][0] if idx+1<len(groups) else len(a)-1
        left=max(int(np.searchsorted(a[:,1],a[rise,1]-margin_ms)),
                 int((prev_end+rise)//2) if idx else 0)
        right=min(int(np.searchsorted(a[:,1],a[fall,1]+margin_ms,side='right')),
                  int((fall+next_start)//2)+1 if idx+1<len(groups) else len(a))
        result=analyze_event(a,left,right,rise,fall,toggles,settle_ms)
        result['event']=idx+1;result['used']=bool(result['valid'] and accepted<count)
        if result['used']:accepted+=1
        results.append(result)
        fig,ax=plt.subplots(2,1,sharex=True,figsize=(12,7),gridspec_kw={'height_ratios':[3,1]})
        draw(ax,a[left:right],True)
        for direction in ('rise','fall'):
            for key,color in [(f'{direction}_pre_cross_ms','C0'),(f'{direction}_post_cross_ms','C1'),
                              ('status_on_ms' if direction=='rise' else 'status_off_ms','C2')]:
                if result.get(key) is not None:
                    for x in ax:x.axvline(result[key]/1000,color=color,ls='--',alpha=.7)
        def fmt(k):return 'N/A' if result.get(k) is None else f'{result[k]:.1f}'
        title=' | '.join(f'{d}: filter {fmt(d+"_filter_half_delay_ms")}, Status-pre {fmt(d+"_status_minus_pre_ms")}, Status-final {fmt(d+"_status_minus_post_ms")} ms' for d in ('rise','fall'))
        ax[0].set_title(f'Event {idx+1}: '+('VALID' if result['valid'] else 'REJECTED')+'\n'+title+'\n'+result['reason'],fontsize=9)
        fig.tight_layout();fig.savefig(folder/f'event_{idx+1:02d}.png',dpi=160);plt.close(fig)
    keys=list(dict.fromkeys(k for r in results for k in r))
    with open(folder/'events.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys or ['event','valid','used','reason']);w.writeheader();w.writerows(results)
    used=[r for r in results if r['used']];summary=[]
    for key in DELAY_KEYS:
        vals=np.array([r[key] for r in used],dtype=float)
        summary.append({'metric':key,'n':len(vals),'median_ms':float(np.median(vals)) if len(vals) else None,
                        'mean_ms':float(np.mean(vals)) if len(vals) else None,
                        'std_ms':float(np.std(vals,ddof=1)) if len(vals)>1 else None,
                        'min_ms':float(np.min(vals)) if len(vals) else None,'max_ms':float(np.max(vals)) if len(vals) else None})
    with open(folder/'summary.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    with open(folder/'results.json','w',encoding='utf-8') as f:
        json.dump({'requested':count,'status_groups':len(groups),'valid_used':accepted,'summary':summary,'events':results},f,indent=2)
    fig,axs=plt.subplots(1,2,figsize=(12,4))
    for ax,direction in zip(axs,('rise','fall')):
        for metric,label in [('filter_half_delay','Filter half'),('status_minus_pre','Status-pre'),('status_minus_post','Status-final')]:
            ax.plot(range(1,len(used)+1),[r[f'{direction}_{metric}_ms'] for r in used],'o-',label=label)
        ax.set_title(direction);ax.set_xlabel('Valid press');ax.set_ylabel('Delay (ms)');ax.grid(alpha=.3);ax.legend()
    fig.tight_layout();fig.savefig(folder/'summary.png',dpi=160);plt.close(fig)
    print(f'Completed: {accepted}/{count} valid presses used; {len(groups)} Status groups found.\nOutput: {folder}')
    if accepted<count:print('WARNING: not enough valid events. Inspect events.csv; record more complete presses.')
    for r in summary:print(r)
    return results,summary,folder

if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--port');g.add_argument('--csv');p.add_argument('--count',type=int,default=10)
    p.add_argument('--baud',type=int,default=115200)
    p.add_argument('--out',default='capture_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')
    p.add_argument('--margin-ms',type=float,default=800)
    p.add_argument('--gap-ms',type=float,default=250)
    args=p.parse_args()
    if args.count<1 or args.margin_ms<200 or args.gap_ms<0:p.error('Invalid count/margins')
    try:
        if args.port:live(args.port,args.baud,args.out)
        analyze_file(args.csv or args.out,args.count,args.margin_ms,args.gap_ms)
    except (OSError,ValueError) as e:p.exit(1,f'Error: {e}\n')
