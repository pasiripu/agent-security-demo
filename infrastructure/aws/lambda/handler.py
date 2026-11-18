"""Two public HTTPS endpoints; only registered inert run data is persisted."""
import datetime
import hashlib
import os
import re
import time
from endpoints.diagnostics.content import render

RUN = re.compile(r'demo-[0-9a-f]{32}\Z')
SCENARIO = re.compile(r'/scenarios/(baseline|injection|control|autonomous)\.txt\Z')
COLLECT = re.compile(r'/collect/(demo-[0-9a-f]{32})\Z')


def response(status, body):
    return {'statusCode':status,'headers':{'content-type':'text/plain; charset=utf-8',
        'cache-control':'no-store','x-content-type-options':'nosniff'},'body':body,'isBase64Encoded':False}


def handle(event, context, table, bucket, s3, role, receiver_origin):
    request=event.get('requestContext',{}).get('http',{})
    if request.get('method')!='GET':
        return response(405,'GET only\n')
    path=event.get('rawPath','')
    query=event.get('rawQueryString','')
    headers={k.lower():v for k,v in event.get('headers',{}).items()}
    variant=None
    if role=='diagnostic':
        match=SCENARIO.fullmatch(path)
        if not match or query:
            return response(404,'Unknown synthetic resource\n')
        variant=match.group(1)
        run_id=headers.get('x-lab-run','')
        if not run_id:
            # Public previews have no run evidence. The zero marker is never registered.
            marker='demo-'+'0'*32
            followup=receiver_origin+'/collect/'+marker+'?marker='+marker
            return response(200,render(variant,followup)+'\nPUBLIC PREVIEW ONLY: no agent run or receipt is implied.\n')
    else:
        match=COLLECT.fullmatch(path)
        if not match:
            return response(404,'Unknown synthetic resource\n')
        run_id=match.group(1)
        if query!='marker='+run_id:
            return response(404,'Unknown synthetic resource\n')
    if not RUN.fullmatch(run_id):
        return response(404,'Unknown synthetic resource\n')
    meta=table.get_item(Key={'run_id':run_id,'event_id':'META'},ConsistentRead=True).get('Item')
    if (not meta or int(meta['expires_at'])<=int(time.time())
            or int(meta.get('active_until',0))<=int(time.time()) or variant and meta['variant']!=variant):
        return response(404,'Unregistered or expired synthetic resource\n')
    if role=='diagnostic':
        body=s3.get_object(Bucket=bucket,Key=meta['log_key'])['Body'].read(32769).decode('utf-8')
        if len(body.encode())>32768 or hashlib.sha256(body.encode()).hexdigest()!=meta['sha256']:
            return response(502,'Diagnostic content integrity check failed\n')
        kind='diagnostic_served'
    else:
        body='Synthetic marker recorded: '+run_id+'\n'
        kind='receiver_receipt'
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    item={'run_id':run_id,'event_id':'EVENT#'+stamp+'#'+context.aws_request_id,
          'time':stamp,'kind':kind,'method':'GET','path':path,'peer':request.get('sourceIp','unknown'),
          'expires_at':int(meta['expires_at']),'transport':'public-https','aws_request_id':context.aws_request_id}
    item.update({'sha256':meta['sha256']} if role=='diagnostic' else {'marker':run_id})
    table.put_item(Item=item)
    return response(200,body)


def lambda_handler(event, context):
    import boto3
    table=boto3.resource('dynamodb').Table(os.environ['RUN_TABLE'])
    try:
        return handle(event,context,table,os.environ['LAB_BUCKET'],boto3.client('s3'),
                      os.environ['ROLE'],os.environ.get('RECEIVER_ORIGIN',''))
    except Exception:
        # Never log raw requests, headers, arbitrary queries, or exception payloads.
        return response(503,'Lab service unavailable\n')
