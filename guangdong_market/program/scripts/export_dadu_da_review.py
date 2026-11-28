"""Export the most recent published day for each Dadu season/error pair as one offline HTML."""
import argparse
from pathlib import Path
import sys

PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAM / 'app'))

from sichuan_v5_da_report import review_data, standalone_report
from sichuan_v5_dadu_da import DAY_SCENARIOS, ERROR_LEVELS, SEASONS, list_days, read_day
from sichuan_v5_dadu_lesson import lesson_html, packed_lesson


def export(root, output, *, beginner=False):
    reports = []
    for scenario, (season, error) in DAY_SCENARIOS.items():
        names = list_days(root, scenario)
        if not names:
            raise ValueError(f'缺少日前出清结果：{scenario}')
        label = f'{SEASONS[season]} · {ERROR_LEVELS[error]}'
        if beginner:
            reports.append(packed_lesson(root, names[0], label))
        else:
            reports.append(review_data(read_day(root, names[0]), label))
    page = lesson_html(reports) if beginner else standalone_report(reports)
    output.write_text(page, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--root', type=Path, default=PROGRAM / 'runs_sichuan_v5_consultation')
    parser.add_argument('--beginner', action='store_true', help='导出可独立使用的入门练习')
    args = parser.parse_args()
    export(args.root, args.output, beginner=args.beginner)
