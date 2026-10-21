"""Matplotlib figure export (optional plotting dependency; no production access)."""
import json
from pathlib import Path
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

root=Path(sys.argv[1]);output=Path(sys.argv[2]);output.mkdir(parents=True,exist_ok=True)
data=json.loads((root/'summary.json').read_text())
font=next((f.fname for f in font_manager.fontManager.ttflist if f.name.startswith('Noto Sans CJK')),None)
if font:plt.rcParams['font.family']=font_manager.FontProperties(fname=font).get_name()
plt.rcParams['axes.unicode_minus']=False
labels={'gateway':'Nginx','spring':'Spring','broker':'Phoenix 브로커','cpp':'C++','storage':'저장소 브로커','sql':'MS SQL','es':'Elasticsearch','redis-primary':'Redis 초기 주 노드'}
fig,axes=plt.subplots(4,2,figsize=(12,13),sharex=True,sharey=True,layout='constrained')
for ax,(name,values) in zip(axes.flat,data['scenarios'].items()):
    pairs=sorted(((int(t),v) for t,v in values['timeline'].items()),key=lambda p:p[0]);x=[t+.5 for t,v in pairs];y=[100*v['p'] for t,v in pairs]
    ax.axvspan(0,5,color='#e46767',alpha=.18,label='5초 정지');ax.plot(x,y,color='#27689f',marker='o',markersize=3,lw=1.5)
    lo=[100*v['wilson95'][0] for t,v in pairs];hi=[100*v['wilson95'][1] for t,v in pairs]
    ax.fill_between(x,lo,hi,color='#27689f',alpha=.13)
    ax.set(title=labels[name],ylim=(-3,103),xlim=(-6,21));ax.grid(alpha=.2);ax.axvline(5,color='#b75d5d',lw=.8,ls='--')
    ax.text(.98,.04,f"전체 {values['overall']['p']:.1%} · n={values['overall']['attempts']}",ha='right',transform=ax.transAxes,fontsize=9)
for ax in axes[-1]:ax.set_xlabel('장애 주입 완료 기준 흐름 시작 시각 (초)')
for ax in axes[:,0]:ax.set_ylabel('전체 사용자 흐름 성공률 (%)')
fig.suptitle('로그인 → 검색 → 접속 → 저장: 시점별 성공률\n각 단계 2초, 클라이언트 재시도 없음 · 같은 시점의 3회 실험을 합산',fontsize=14)
from matplotlib.patches import Patch
fig.legend(handles=[Patch(color='#e46767',alpha=.18,label='빨강: 명목상 정지 0~5초'),Patch(color='#27689f',alpha=.13,label='파랑 띠: 이항 95% 참고 구간 (요청 간 상관 존재)')],loc='outside lower center',ncol=2,fontsize=9)
fig.savefig(output/'chaos-userflow-timeline.png',dpi=180);fig.savefig(output/'chaos-userflow-timeline.pdf');fig.savefig(output/'chaos-userflow-timeline.svg');plt.close(fig)
