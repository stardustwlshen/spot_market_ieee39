"""Inspect, then explicitly adopt missing repository research cost assumptions."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from smm.cost_archive import apply_cost_archive_import, prepare_cost_archive_import
from smm.scenario import scenario_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', default='默认')
    parser.add_argument('--apply-research-assumptions', action='store_true',
                        help='明确采用仓库合成研究假设，只写入缺失成本档案')
    args = parser.parse_args()
    try:
        plan = prepare_cost_archive_import(scenario_path(args.scenario), ROOT)
        print('来源项目:', plan.source)
        print('目标场景:', plan.target)
        print('合成研究假设，非真实机组实测成本；已有档案、报价与物理边界不会被覆盖。')
        for name, content in zip(plan.missing, plan.contents):
            print('待补充:', name, 'SHA256:', hashlib.sha256(content).hexdigest())
        if not plan.missing:
            print('成本档案已齐全，无需补充。')
        elif args.apply_research_assumptions:
            print('已补充:', '、'.join(apply_cost_archive_import(plan)))
            print('刷新网页后，重新生成并核对报价预览，再选择是否采用。')
        else:
            print('仅检查，未写入。核对上述路径后，加 --apply-research-assumptions 明确采用。')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(1, f'未完成补档: {exc}\n')


if __name__ == '__main__':
    main()
