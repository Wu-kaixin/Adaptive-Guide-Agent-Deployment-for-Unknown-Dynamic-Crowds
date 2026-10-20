"""Presentation only: draw values from the prespecified saved analysis and ledger."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def save(fig,path):
    fig.savefig(path.with_suffix('.png'),dpi=140)
    fig.savefig(path.with_suffix('.svg'))
    svg=path.with_suffix('.svg')
    svg.write_text('\n'.join(x.rstrip() for x in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    plt.close(fig)


def main(report):
    report=Path(report); a=json.loads((report/'analysis.json').read_text(encoding='utf-8'))
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    primary=a['primary']
    for i,scene in enumerate(('elongated','tight_u','wide_u')):
        pairs=[p for p in primary['pairs'] if p['scene']==scene]
        values=[p['difference'] for p in pairs]
        axes[0].scatter(values,i+np.linspace(-.13,.13,len(values)),s=22,color='#247cab',alpha=.85)
        if values: axes[0].scatter(np.mean(values),i,marker='D',s=65,color='#222222',zorder=4)
    estimate=primary['equal_scene_mean_difference']; interval=primary['bootstrap']['interval']
    if estimate is not None:
        axes[0].scatter(estimate,3,color='#cf7036',marker='D',s=75,zorder=4)
        if interval is not None: axes[0].errorbar(estimate,3,xerr=[[estimate-interval[0]],[interval[1]-estimate]],fmt='none',color='#cf7036',capsize=4)
    axes[0].set_yticks(range(4),[s+f" (n={primary['by_scene'][s]['n_pairs']})" for s in ('elongated','tight_u','wide_u')]+['Equal-scene estimate + 95% CI'])
    axes[0].invert_yaxis(); axes[0].axvline(0,color='#999999',lw=.8)
    axes[0].set_title('Primary: hotspot B qualified-target pairs')
    axes[0].set_xlabel('Final planned demand cost: projected - equal (m²)')
    names=['Total demand','Hotspot excess','Background mass','Background arc']
    values=[a['secondary']['hotspot5'][k]['equal_scene_mean_relative_pct'] for k in ('demand','excess','background_mass','background_arc')]
    axes[1].barh(np.arange(4),values,color=['#247cab' if v<0 else '#cf7036' for v in values],height=.55)
    axes[1].set_yticks(range(4),names); axes[1].invert_yaxis(); axes[1].axvline(0,color='#999999',lw=.8)
    for i,v in enumerate(values):
        if v < -1:
            axes[1].text(v/2,i,f'{v:+.3f}%',va='center',ha='center',fontsize=9,color='white')
        else:
            axes[1].text(v,i,f' {v:+.3f}%',va='center',ha='left' if v>=0 else 'right',fontsize=9)
    axes[1].set_xlim(-4.8,3.4)
    axes[1].set_xlabel('Scene-weighted relative change (%); negative = lower cost')
    axes[1].set_title('Secondary: distinct demand and background metrics')
    save(fig,report/'paired_demand')
    ledger=list(csv.DictReader((report/'tasks_240.csv').open(encoding='utf-8-sig')))
    groups=[('ones','equal_arc'),('ones','projected_mass'),('hotspot5','equal_arc'),('hotspot5','projected_mass')]
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    bottom=np.zeros(4)
    colors={'SUCCESS':'#419b81','INITIALIZATION_INVALID':'#b6bcc4','BOUNDARY_INVALID':'#e8b270',
            'TIMEOUT':'#d56b5f','REPLAN_EXHAUSTED':'#bb6558','PLAN_SEARCH_EXHAUSTED':'#a88cbd'}
    for status in sorted({r['status'] for r in ledger}):
        values=np.array([sum(r['demand']==d and r['method']==m and r['status']==status for r in ledger) for d,m in groups])
        axes[0].bar(range(4),values,bottom=bottom,color=colors.get(status,'#8888aa'),label=status)
        for i,v in enumerate(values):
            if v: axes[0].text(i,bottom[i]+v/2,str(v),ha='center',va='center',fontsize=10)
        bottom+=values
    axes[0].set_xticks(range(4),['A Equal','A Projected','B Equal','B Projected'])
    axes[0].set_ylabel('Rows (all 60 physical units per condition/method)')
    axes[0].set_title('Full accounting, including shared rejections'); axes[0].legend(fontsize=7)
    for scene,color in [('elongated','#247cab'),('tight_u','#cf7036'),('wide_u','#419b81')]:
        pairs=[p for p in a['motion_pairs']['hotspot5']['pairs'] if p['scene']==scene]
        axes[1].scatter([p['time_difference_s'] for p in pairs],[p['travel_difference_m'] for p in pairs],label=scene,color=color,s=25)
    axes[1].axvline(0,color='#aaa',lw=.8); axes[1].axhline(0,color='#aaa',lw=.8)
    axes[1].set_xlabel('Simulation time difference, projected - equal (s)')
    axes[1].set_ylabel('Actual total travel difference (m)')
    axes[1].set_title(f"B motion costs: both-success subset, n={a['motion_pairs']['hotspot5']['n_both_success']}")
    axes[1].legend(fontsize=8)
    save(fig,report/'deployment_accounting')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--report',required=True)
    main(p.parse_args().report)
