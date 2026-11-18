"""AWS CLI JSON calls; secret-bearing JSON is passed through stdin, never argv."""
import json
import subprocess


def aws(region, service, operation, payload=None, timeout=45):
    cmd=['aws','--region',region,service,operation,'--output','json','--no-cli-pager']
    if payload is not None:
        cmd+=['--cli-input-json','file:///dev/stdin']
    result=subprocess.run(cmd,input=None if payload is None else json.dumps(payload),
                          capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        # AWS error bodies can include parameter values. Do not echo them.
        raise RuntimeError(f'AWS {service} {operation} failed (exit {result.returncode}); inspect IAM/resource configuration')
    return json.loads(result.stdout) if result.stdout.strip() else {}
