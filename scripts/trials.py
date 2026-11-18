"""Balanced, repeatable real-model cohort. No manufactured model events."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import random
import uuid
from agent.common import load_config
from agent.credentials import api_key
from agent.run import prepare, execute


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='local/config.json')
    p.add_argument('--repeats',type=int,default=5,help='Additional trials per condition')
    p.add_argument('--parallel',type=int,default=1,help='Independent CLI processes, 1..4')
    p.add_argument('--seed',type=int,default=417)
    a=p.parse_args()
    if not 1<=a.repeats<=25 or not 1<=a.parallel<=4:
        p.error('repeats must be 1..25 and parallel 1..4')
    cfg=load_config(a.config)
    if not api_key():
        p.error('Configure the API key privately first')
    cohort='cohort-'+uuid.uuid4().hex
    schedule=['baseline','injection','control','autonomous']*a.repeats
    random.Random(a.seed).shuffle(schedule)
    print(json.dumps({'cohort':cohort,'seed':a.seed,'schedule':schedule,'parallel':a.parallel,
                      'maximum_aggregate_cli_cap_usd':len(schedule)*cfg['max_budget_usd']}),flush=True)
    def trial(item):
        order,variant=item
        folder,meta=prepare(cfg,variant)
        meta.update(cohort_id=cohort,cohort_order=order,cohort_seed=a.seed,cohort_parallel=a.parallel)
        outcome=execute(cfg,folder,meta)
        return {'run_id':meta['run_id'],'variant':variant,'outcome':outcome}
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        futures=[pool.submit(trial,item) for item in enumerate(schedule)]
        for future in as_completed(futures):
            print(json.dumps(future.result()),flush=True)


if __name__=='__main__':
    main()
