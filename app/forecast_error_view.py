"""Read only executed-input diagnostics. Never obtains unexecuted ground truth."""
from pathlib import Path
import json
import csv
import re
from datetime import date
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

KIND_CN={'wind':'风电可用功率','pv':'光伏可用功率','load':'负荷','natural_inflow':'天然来水'}
STAGE_CN={'DA':'日前','ID':'日内参考','RT':'实时原发布预测'}


_HASH=re.compile(r'^[0-9a-f]{64}$')
_INT=re.compile(r'^(?:0|[1-9][0-9]*)$')
_TIME=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.0+)?(?:Z|[+-]\d{2}:\d{2})$')
_RTOL=1e-10
_ATOL=1e-10


def _status_integer(status,key,lower,upper):
    value=status.get(key)
    if type(value) is not int or not lower<=value<=upper:raise ValueError('诊断范围整数无效：'+key)
    return value


def _hashes(values,label):
    if any(not isinstance(x,str) or not _HASH.fullmatch(x) for x in values):raise ValueError(label+'哈希缺失或无效')


def _integer_column(frame,key,lower=0,upper=95):
    text=frame[key]
    if not text.map(lambda x:isinstance(x,str) and _INT.fullmatch(x) is not None).all():
        raise ValueError('整数列不能含布尔、小数或空值：'+key)
    numbers=pd.to_numeric(text,errors='raise')
    if not numbers.between(lower,upper).all():raise ValueError('整数列超出范围：'+key)
    frame[key]=numbers.astype('int64')


def _numeric_columns(frame,keys,optional=()):
    for key in keys:
        raw=frame[key]
        blank=raw.eq('')
        if blank.any() and key not in optional:raise ValueError('数值列缺失：'+key)
        values=pd.to_numeric(raw.where(~blank,np.nan),errors='raise').astype(float)
        if not np.isfinite(values[~blank]).all():raise ValueError('数值列非有限：'+key)
        frame[key]=values


def _timestamps(values,label):
    text=pd.Series(values,copy=False)
    if not text.map(lambda x:isinstance(x,str) and _TIME.fullmatch(x) is not None).all():
        raise ValueError(label+'须是带时区的整秒时间')
    parsed=pd.to_datetime(text,format='ISO8601',utc=True,errors='raise')
    seconds=parsed.astype('int64').to_numpy()//1_000_000_000
    if (seconds%900).any():raise ValueError(label+'不在15分钟网格')
    return seconds


def _close(actual,expected,label,allow_nan=False):
    if not np.allclose(np.asarray(actual,float),np.asarray(expected,float),rtol=_RTOL,atol=_ATOL,equal_nan=allow_nan):
        raise ValueError(label+'与原明细重算不一致')


def _read_table(path):
    with path.open(encoding='utf-8-sig',newline='') as f:header=next(csv.reader(f),[])
    if not header or len(header)!=len(set(header)):raise ValueError('预测误差文件表头缺失或重复')
    # Preserve integer lexical form: False or 1.0 cannot silently become period 0/1.
    return pd.read_csv(path,dtype=str,keep_default_na=False,encoding='utf-8-sig')


def _expected_summary(actual,detail):
    rows=[];means=actual.groupby('resource_id').actual_input.mean().to_dict()
    def add(keys,group,bucket):
        stage,uid,kind,unit=keys
        error=(group.actual_input-group.forecast_input).to_numpy(float)
        mean=float(means[uid]);rmse=float(np.sqrt(np.mean(error**2)))
        rows.append(dict(stage=stage,resource_id=uid,kind=kind,unit=unit,lead_bucket=bucket,
            forecast_comparisons=len(group),unique_executed_periods=int(group.period.nunique()),
            mae=float(np.mean(abs(error))),rmse=rmse,bias_actual_minus_forecast=float(np.mean(error)),
            actual_mean_unique_periods=mean,nrmse_by_executed_mean=rmse/mean if mean>0 else np.nan))
    keys=['stage','resource_id','kind','unit']
    for key,group in detail.groupby(keys+['lead_bucket'],sort=True):add(key[:4],group,key[4])
    for key,group in detail.groupby(keys,sort=True):add(key,group,'all')
    return pd.DataFrame(rows)


def load_forecast_diagnostics(directory):
    """Validate schema-2 context status and CSV consistency, not future truth.

    Source authenticity remains the archived backend context's responsibility.
    This reader independently recomputes every displayed summary from detail.
    """
    directory=Path(directory)
    empty={'valid':False,'reason':'本次结果未提供可核验的预测误差记录。'}
    try:
        path=directory/'forecast_error_status.json'
        if not path.is_file():return empty
        status=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(status,dict):raise ValueError('诊断状态结构无效')
        if status.get('valid') is not True:
            errors=status.get('errors',[])
            return dict(empty,reason='；'.join(str(e) for e in errors) if isinstance(errors,list) and errors else '原始发布或实际执行证据不完整。')
        schema=status.get('schema_version')
        if type(schema) is not int or schema!=2:
            raise ValueError('历史误差记录尚未完成新版时间元数据核验。' if schema==1 else '预测误差核验版本不受支持。')
        if status.get('model')!='causal_physical_v3' or status.get('status')!='verified_executed_prefix':
            raise ValueError('预测误差版本或执行范围未核实')
        if status.get('errors')!=[]:raise ValueError('诊断通过状态仍含未解决错误')
        if status.get('context_origin') not in ('native_case_snapshot','explicit_frozen_archive'):
            raise ValueError('冻结上下文来源未核实')
        _hashes([status.get(k) for k in ('context_hash','configuration_hash','source_manifest_hash','da_selected_inputs_hash')],'冻结上下文')
        records=status.get('source_records')
        if not isinstance(records,list) or (status['context_origin']=='explicit_frozen_archive' and not records):
            raise ValueError('历史派生报告缺原冻结来源记录')
        for record in records:
            if not isinstance(record,dict) or not isinstance(record.get('file'),str) or not record['file']:raise ValueError('原冻结来源记录无效')
            _hashes([record.get('sha256')],'原冻结来源')
        if status.get('current_measurement_is_not_RT_prediction') is not True or status.get('error_sign')!='actual_minus_original_issued_forecast' or status.get('scope')!='selected_issued_targets_intersected_with_executed_prefix_only' or status.get('lead_reference')!='interval_midpoint_minus_issue_time':
            raise ValueError('原预测、首段观测或提前时间口径未核实')
        expected_semantics={
            'truth':'executed_RT_current_interval_perfect_measurement_of_available_inputs_not_dispatch',
            'weighting':'equal forecast-target pairs within each stage/resource/lead bucket; overlapping windows repeat comparisons, never executed quantities',
            'normalized_error_denominator':'resource mean over unique executed input periods; unavailable when mean is zero',
        }
        if any(status.get(key)!=value for key,value in expected_semantics.items()):raise ValueError('实际输入或汇总权重口径未核实')
        n=_status_integer(status,'executed_periods',1,96)
        _status_integer(status,'expected_periods',96,96)
        resource_count=_status_integer(status,'resource_count',1,100000)
        actual_count=_status_integer(status,'actual_input_rows',1,96*resource_count)
        comparison_count=_status_integer(status,'forecast_comparisons',1,100000000)
        publications=_status_integer(status,'publication_windows',1,193)
        excluded=_status_integer(status,'excluded_unexecuted_publication_windows',0,96)
        if type(status.get('full_day')) is not bool or status['full_day']!=(n==96):raise ValueError('完整日标记与执行范围不同')
        if not isinstance(status.get('operating_date'),str) or date.fromisoformat(status['operating_date']).isoformat()!=status['operating_date']:
            raise ValueError('运行日期无效')
        day=int(_timestamps([status.get('day_start_utc')],'运行日开始')[0])
        names={'actual':'executed_input_actual.csv','detail':'forecast_error_detail.csv','summary':'forecast_error_summary.csv'}
        tables={key:_read_table(directory/name) for key,name in names.items()}
        actual,detail,summary=(tables[k] for k in ('actual','detail','summary'))
        common={'operating_date','resource_id','kind','unit','period','valid_start_utc','valid_end_utc'}
        summary_keys=['stage','resource_id','kind','unit','lead_bucket']
        summary_metrics=['mae','rmse','bias_actual_minus_forecast','actual_mean_unique_periods','nrmse_by_executed_mean']
        for frame,required in [(actual,common|{'actual_input','actual_source','selected_inputs_hash'}),
                (detail,common|{'stage','issue_time_utc','window_start','lead_midpoint_h','lead_bucket','forecast_input','actual_input','error_actual_minus_forecast','absolute_error','squared_error','forecast_id','information_set_id','actual_selected_inputs_hash'}),
                (summary,set(summary_keys+summary_metrics+['forecast_comparisons','unique_executed_periods']))]:
            if not required<=set(frame) or frame.empty:raise ValueError('预测误差记录缺字段或为空')
        for frame in (actual,detail):
            if set(frame.operating_date)!={status['operating_date']}:raise ValueError('CSV日期与诊断范围不同')
            _integer_column(frame,'period',0,n-1)
        _integer_column(detail,'window_start')
        _integer_column(summary,'forecast_comparisons',1,comparison_count)
        _integer_column(summary,'unique_executed_periods',1,n)
        _numeric_columns(actual,['actual_input'])
        _numeric_columns(detail,['forecast_input','actual_input','error_actual_minus_forecast','absolute_error','squared_error','lead_midpoint_h'])
        _numeric_columns(summary,summary_metrics,optional=('nrmse_by_executed_mean',))
        if (actual.actual_input<0).any() or (detail[['forecast_input','actual_input','absolute_error','squared_error']]<0).any().any():raise ValueError('输入或绝对误差不能为负')
        keys=['resource_id','period']
        if actual.duplicated(keys).any() or len(actual)!=actual_count or actual.resource_id.nunique()!=resource_count:
            raise ValueError('实际输入重复或记录数不符')
        if not all(set(g.period)==set(range(n)) for _,g in actual.groupby('resource_id')):raise ValueError('实际输入不是完整已执行前缀')
        identities=actual[['resource_id','kind','unit']].drop_duplicates()
        if len(identities)!=resource_count or identities.resource_id.eq('').any():raise ValueError('资源身份不唯一')
        units={'wind':'MW','pv':'MW','load':'MW','natural_inflow':'m3/s'}
        if any(r.kind not in units or r.unit!=units[r.kind] for r in identities.itertuples()):raise ValueError('资源类型或单位无效')
        if set(actual.actual_source)!={'current_interval_perfect_measurement'}:raise ValueError('实际输入不是已执行首段观测')
        for column in ('selected_inputs_hash',):_hashes(actual[column],'实际输入')
        for column in ('forecast_id','information_set_id','actual_selected_inputs_hash'):_hashes(detail[column],'预测比较')
        if (actual.groupby('period').selected_inputs_hash.nunique()!=1).any():raise ValueError('同段实际资源不来自同一选中输入')
        for frame,label in ((actual,'实际输入'),(detail,'预测比较')):
            start=_timestamps(frame.valid_start_utc,label+'有效开始');end=_timestamps(frame.valid_end_utc,label+'有效结束')
            if not np.array_equal(start,day+frame.period.to_numpy()*900) or not np.array_equal(end,start+900):raise ValueError(label+'时间与时段不一致')
        if not set(detail.stage)<={'DA','ID','RT'}:raise ValueError('发布阶段未知')
        if len(detail)!=comparison_count:raise ValueError('预测比较条数不符')
        if detail.duplicated(['stage','window_start','resource_id','period']).any():raise ValueError('预测比较重复')
        issue=_timestamps(detail.issue_time_utc,'发布时间')
        window=day+detail.window_start.to_numpy()*900
        if (issue<day).any() or (issue>window).any() or (detail.period<detail.window_start).any():raise ValueError('发布窗口与目标段先后关系无效')
        realtime_or_da=detail.stage.isin(['DA','RT']).to_numpy()
        if not np.array_equal(issue[realtime_or_da],window[realtime_or_da]):raise ValueError('日前/实时发布时间不等于窗口开始')
        lead=(day+detail.period.to_numpy()*900+450-issue)/3600
        _close(detail.lead_midpoint_h,lead,'提前时间')
        bucket=np.select([lead<1,lead<4,lead<12],['0-1h','1-4h','4-12h'],default='12-24h')
        if not np.array_equal(detail.lead_bucket,bucket):raise ValueError('提前时间分桶与明细不符')
        matched=detail.merge(actual[keys+['actual_input','kind','unit','selected_inputs_hash']],on=keys,how='left',suffixes=('','_executed'),validate='many_to_one')
        if not np.isfinite(matched.actual_input_executed).all():raise ValueError('预测包含未执行输入')
        if not np.array_equal(matched.kind,matched.kind_executed) or not np.array_equal(matched.unit,matched.unit_executed):raise ValueError('资源类型或单位不一致')
        if not np.array_equal(matched.actual_selected_inputs_hash,matched.selected_inputs_hash):raise ValueError('实际输入来源与执行表不同')
        _close(matched.actual_input,matched.actual_input_executed,'实际输入')
        error=(matched.actual_input-matched.forecast_input).to_numpy()
        _close(matched.error_actual_minus_forecast,error,'误差值')
        _close(matched.absolute_error,abs(error),'绝对误差')
        _close(matched.squared_error,error**2,'平方误差')
        window_keys=['stage','window_start']
        for key,group in detail.groupby(window_keys,sort=False):
            if len(group[['issue_time_utc','forecast_id','information_set_id']].drop_duplicates())!=1:raise ValueError('同一发布窗口的来源不一致')
            if set(group.resource_id)!=set(actual.resource_id):raise ValueError('发布窗口缺资源')
            if any(set(g.period)!=set(range(key[1],n)) for _,g in group.groupby('resource_id')):raise ValueError('发布窗口的已执行目标覆盖不完整')
        if set(detail.loc[detail.stage=='DA','window_start'])!={0} or set(detail.loc[detail.stage=='RT','window_start'])!=set(range(n)):
            raise ValueError('日前或实时发布窗口不完整')
        if detail[window_keys].drop_duplicates().shape[0]+excluded!=publications:raise ValueError('发布窗口计数与排除范围不同')
        for _,group in detail.groupby('forecast_id'):
            if len(group[['issue_time_utc','information_set_id']].drop_duplicates())!=1:raise ValueError('同一预测哈希的发布时间或信息集不同')
        if summary.duplicated(summary_keys).any():raise ValueError('阶段或提前时间汇总重复')
        expected=_expected_summary(actual,detail)
        left=summary.set_index(summary_keys).sort_index();right=expected.set_index(summary_keys).sort_index()
        if not left.index.equals(right.index):raise ValueError('阶段或提前时间汇总缺失/多余')
        for column in ('forecast_comparisons','unique_executed_periods'):
            if not np.array_equal(left[column],right[column]):raise ValueError('汇总样本条数或权重错误：'+column)
        for column in summary_metrics:_close(left[column],right[column],'阶段指标 '+column,allow_nan=column=='nrmse_by_executed_mean')
        return dict(valid=True,status=status,verification_scope='schema2_context_status_and_csv_internal_consistency',**tables)
    except (OSError,ValueError,KeyError,TypeError,OverflowError,pd.errors.ParserError) as exc:
        return dict(empty,reason=str(exc))


def render_forecast_diagnostics(directory):
    data=load_forecast_diagnostics(directory)
    if not data['valid']:
        # No empty diagnostic card for old runs. A present-but-invalid record is
        # still actionable evidence and remains a visible reliability warning.
        if (Path(directory)/'forecast_error_status.json').is_file():
            st.warning('预测误差记录未通过核验：'+data['reason'])
        return data
    with st.expander('预测与已执行实际输入',expanded=False):
        status=data['status'];detail=data['detail'];summary=data['summary']
        st.caption(f"{status['operating_date']} · 已执行 {status['executed_periods']} / {status.get('expected_periods',96)} 段；未执行段不显示实际值。")
        st.caption('风光为可用功率，不是机组发电出力。实时误差对比原发布预测，不把首段完美测量当零误差。')
        st.caption('统计发布包中后来已执行的时段，不代表每次优化使用了全部预测。')
        if status['context_origin']=='explicit_frozen_archive':st.caption('此表由原冻结来源只读回算，已核验新版时间元数据与表内一致性。')
        resources=data['actual'][['resource_id','kind','unit']].drop_duplicates().set_index('resource_id')
        uid=st.selectbox('误差分析资源',list(resources.index),format_func=lambda x:x+' · '+KIND_CN.get(resources.loc[x,'kind'],resources.loc[x,'kind']),key='forecast_error_resource:'+str(directory))
        stages=[s for s in ('DA','ID','RT') if s in set(detail.loc[detail.resource_id==uid,'stage'])]
        stage=st.radio('原预测发布阶段',stages,format_func=STAGE_CN.get,horizontal=True,key='forecast_error_stage:'+str(directory))
        selected=detail[(detail.resource_id==uid)&(detail.stage==stage)]
        issues=selected[['issue_time_utc','window_start','forecast_id']].drop_duplicates()
        if len(issues)>1:
            labels={i:f"{r.issue_time_utc} · 第{int(r.window_start)}段发布" for i,r in issues.iterrows()}
            index=st.selectbox('查看一次原始发布',list(labels),format_func=labels.get,key='forecast_error_issue:'+str(directory)+':'+uid+':'+stage)
            row=issues.loc[index];selected=selected[(selected.forecast_id==row.forecast_id)&(selected.window_start==row.window_start)]
        selected=selected.sort_values('period')
        unit='m³/s' if resources.loc[uid,'unit']=='m3/s' else resources.loc[uid,'unit']
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=selected.period,y=selected.forecast_input,name='原发布预测',mode='lines',line=dict(color='#2563eb',dash='dash')))
        fig.add_trace(go.Scatter(x=selected.period,y=selected.actual_input,name='已执行实际输入',mode='lines',line=dict(color='#dc2626')))
        fig.update_layout(height=360,margin=dict(l=15,r=15,t=25,b=20),xaxis_title='运行日时段（从0开始）',yaxis_title=unit,legend=dict(orientation='h'))
        st.plotly_chart(fig,width='stretch',key='forecast_error_chart:'+str(directory)+':'+uid+':'+stage, config={'modeBarButtonsToRemove': ['toImage']})
        overall=summary[(summary.resource_id==uid)&(summary.stage==stage)&(summary.lead_bucket=='all')]
        st.markdown('**该资源本阶段整体误差**')
        if len(overall)==1:
            row=overall.iloc[0]
            for col,label,key in zip(st.columns(3),['平均绝对误差','均方根误差','平均偏差（实际−预测）'],['mae','rmse','bias_actual_minus_forecast']):
                col.metric(label,f'{row[key]:.5g} {unit}')
            st.caption(f"本阶段共{int(row.forecast_comparisons)}对预测比较，覆盖{int(row.unique_executed_periods)}个实际时段；重叠窗口重复比较，不重复累计电量。")
        else:st.info('该资源阶段汇总未提供。')
        shown=summary[summary.lead_bucket=='all'].copy()
        shown['stage']=shown['stage'].map(STAGE_CN);shown['kind']=shown['kind'].map(KIND_CN);shown['unit']=shown['unit'].replace({'m3/s':'m³/s'})
        shown=shown[['stage','resource_id','kind','unit','mae','rmse','bias_actual_minus_forecast','unique_executed_periods','forecast_comparisons']]
        shown.columns=['阶段','资源','输入类型','单位','MAE','RMSE','平均偏差','实际时段数','比较对数']
        st.dataframe(shown,hide_index=True,width='stretch')
        with st.expander('按提前时间与完整数据'):
            st.dataframe(summary[summary.resource_id==uid],hide_index=True,width='stretch')
            for name,frame in [('实际输入',data['actual']),('预测误差明细',detail),('阶段与提前时间汇总',summary)]:
                st.caption(name)
                st.dataframe(frame, hide_index=True, width='stretch')
        return data
