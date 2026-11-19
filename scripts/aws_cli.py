"""AWS CLI JSON calls through private temporary input files, never secret argv."""
import json
import subprocess
import tempfile
from contextlib import nullcontext


def aws(region, service, operation, payload=None, timeout=45):
    cmd=['aws','--region',region,service,operation,'--output','json','--no-cli-pager']
    # Some CLI releases read the parameter file twice, so non-seekable stdin fails.
    # NamedTemporaryFile is 0600 and its context removes it on success or error.
    context=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json') if payload is not None else nullcontext()
    with context as input_file:
        if input_file is not None:
            json.dump(payload,input_file);input_file.flush()
            cmd+=['--cli-input-json','file://'+input_file.name]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        # AWS error bodies can include parameter values. Do not echo them.
        raise RuntimeError(f'AWS {service} {operation} failed (exit {result.returncode}); inspect IAM/resource configuration')
    return json.loads(result.stdout) if result.stdout.strip() else {}
