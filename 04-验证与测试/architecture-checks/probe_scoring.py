"""用成熟sklearn流程验证相关样本泄漏和区间有限样本边界；全部为合成数据。"""
import argparse
import json
import math
from importlib.metadata import version
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold, KFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def main():
    rng=np.random.default_rng(42)
    groups=np.repeat(np.arange(24),8)
    X=np.eye(24)[groups]
    true_by_group=rng.uniform(40,95,24)
    y=true_by_group[groups]+rng.normal(0,.1,len(groups))
    pipeline=make_pipeline(StandardScaler(),Ridge(alpha=.1))
    naive=cross_val_predict(pipeline,X,y,cv=KFold(6,shuffle=True,random_state=42))
    strict=cross_val_predict(pipeline,X,y,groups=groups,cv=GroupKFold(6))
    naive_mae=float(mean_absolute_error(y,naive));strict_mae=float(mean_absolute_error(y,strict))
    assert strict_mae>10*naive_mae
    for train,test in GroupKFold(6).split(X,y,groups):
        assert not set(groups[train]) & set(groups[test])
    def quantile(residuals,coverage):
        n=len(residuals);rank=math.ceil((n+1)*coverage)
        return None if rank>n else sorted(residuals)[rank-1]
    short=quantile([1,2,3,4,5],.9)
    enough=quantile(list(range(1,22)),.9)
    assert short is None and enough==20
    return dict(scope='合成样本机制实验；数值只说明泄漏与有限样本问题，不是本产品精度',passed=True,versions={p:version(p) for p in ('scikit-learn','numpy')},synthetic_samples=len(groups),groups=24,naive_random_split_mae=naive_mae,group_split_mae=strict_mae,small_calibration_90pct_interval='有限样本分位次序超出样本数，拒绝声称有限半宽',calibration_21_quantile_90pct=enough,external_model_calls=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();result=main();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
