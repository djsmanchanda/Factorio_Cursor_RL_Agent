# Path: tests/test_laya_context_suite.py | Purpose: Ensure suite reporting separates fixture domains, invalid pairs and interrupted outcomes.
from tools.run_laya_context_suite import analyze
from tools.laya_experiment_helper import text_output,MODEL


def test_matched_reporting_quarantines_tool_failures_and_interruptions():
 rows=[dict(kind='raw',phase='C1',case=None,variant='baseline',seed=1,status='finished',target_met=True,target_ticks=100,infrastructure_error=False),
       dict(kind='raw',phase='C1',case=None,variant='buffer-1',seed=1,status='finished',target_met=False,target_ticks=None,infrastructure_error=True),
       dict(kind='raw',phase='C1',case=None,variant='buffer-1',seed=2,status='interrupted',target_met=True,target_ticks=90,infrastructure_error=False)]
 report=analyze(rows)
 assert report['matched_pairs'][0]['usable'] is False
 variant=next(x for x in report['variants'] if x['variant']=='buffer-1')
 assert variant['attempts']==2 and variant['usable']==0 and variant['rate'] is None
 assert report['adoption'].startswith('none')


def test_helper_extracts_only_final_text_and_preserves_exact_requested_model():
 assert MODEL=='opencode-go/muse-spark-1.3-contributor'
 assert text_output('{"type":"reasoning","part":{"text":"private"}}\n{"type":"text","part":{"text":"findings"}}')=='findings'
