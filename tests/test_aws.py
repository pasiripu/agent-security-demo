"""Offline contract tests; these do not claim live AWS deployment success."""
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock
from agent.common import ROOT, urls
from agent.http_tool import LabHTTP
from endpoints.diagnostics.content import render
from scripts.aws_instance_job import validate_issue, reserve
from scripts.github_setup import public_files

spec=importlib.util.spec_from_file_location('lambda_handler',ROOT/'infrastructure/aws/lambda/handler.py')
handler=importlib.util.module_from_spec(spec); spec.loader.exec_module(handler)
RUN='demo-'+'a'*32
A='https://example-a.lambda-url.us-west-2.on.aws'
B='https://example-b.lambda-url.us-west-2.on.aws'


class Table:
    def __init__(self,meta): self.meta=meta; self.events=[]
    def get_item(self,**kw):
        return {'Item':self.meta} if kw['Key']['run_id']==RUN and self.meta else {}
    def put_item(self,**kw): self.events.append(kw['Item'])


class AWSContracts(unittest.TestCase):
    def setUp(self):
        self.body=render('injection',B+'/collect/'+RUN+'?marker='+RUN)
        self.table=Table({'variant':'injection','log_key':'logs/'+RUN+'.txt',
                          'sha256':hashlib.sha256(self.body.encode()).hexdigest(),
                          'expires_at':int(time.time())+86400,'active_until':int(time.time())+600})
        self.s3=types.SimpleNamespace(get_object=lambda **kw:{'Body':io.BytesIO(self.body.encode())})
        self.context=types.SimpleNamespace(aws_request_id='aws-request-123')

    def call(self,path='/scenarios/injection.txt',query='',headers=None,role='diagnostic',method='GET'):
        return handler.handle({'rawPath':path,'rawQueryString':query,'headers':headers or {},
            'requestContext':{'http':{'method':method,'sourceIp':'203.0.113.42'}}},
            self.context,self.table,'synthetic-bucket',self.s3,role,B)

    def test_public_preview_creates_no_receipt(self):
        result=self.call(); self.assertEqual(result['statusCode'],200)
        self.assertIn('PUBLIC PREVIEW',result['body']); self.assertEqual(self.table.events,[])

    def test_registered_delivery_hash_and_receipt(self):
        result=self.call(headers={'X-Lab-Run':RUN})
        self.assertEqual(result['body'],self.body)
        self.assertEqual(self.table.events[0]['sha256'],hashlib.sha256(self.body.encode()).hexdigest())
        self.assertEqual(self.table.events[0]['kind'],'diagnostic_served')
        self.assertEqual(self.table.events[0]['aws_request_id'],'aws-request-123')

    def test_only_exact_registered_marker_is_recorded(self):
        path='/collect/'+RUN
        self.assertEqual(self.call(path,'marker='+RUN,role='receiver')['statusCode'],200)
        self.assertEqual(self.table.events[0]['kind'],'receiver_receipt')
        self.assertEqual(self.call(path,'marker='+RUN+'&extra=1',role='receiver')['statusCode'],404)
        self.assertEqual(len(self.table.events),1)

    def test_unregistered_expired_and_closed_runs_rejected(self):
        for field in ('expires_at','active_until'):
            with self.subTest(field=field):
                self.table.meta[field]=0
                self.assertEqual(self.call(headers={'x-lab-run':RUN})['statusCode'],404)
                self.table.meta[field]=int(time.time())+600
        self.assertEqual(self.call(headers={'x-lab-run':'demo-'+'b'*32})['statusCode'],404)
        self.assertEqual(self.table.events,[])

    def test_content_corruption_is_not_delivery(self):
        self.table.meta['sha256']='0'*64
        self.assertEqual(self.call(headers={'x-lab-run':RUN})['statusCode'],502)
        self.assertEqual(self.table.events,[])

    def test_exact_paths_methods_queries(self):
        for path,query,method in [('/scenarios/injection.txt/','','GET'),('/scenarios/injection.txt','x=1','GET'),
                                  ('/scenarios/injection.txt','','POST'),('/scenarios/other.txt','','GET')]:
            self.assertNotEqual(self.call(path,query,method=method)['statusCode'],200)
        self.assertEqual(self.table.events,[])

    def test_issue_validation_preserves_untrusted_text_as_data(self):
        cfg={'github_repository':'pasiripu/agent-security-demo','diagnostic_origin':A}
        job={'repository':cfg['github_repository'],'number':42,'url':'https://github.com/'+cfg['github_repository']+'/issues/42',
             'title':'$(never execute this)','body':'Diagnostics: '+A+'/scenarios/injection.txt\n`touch /tmp/never`'}
        variant,issue=validate_issue(job,cfg,42)
        self.assertEqual(variant,'injection'); self.assertEqual(issue['title'],job['title'])
        for body in (job['body'].replace('.txt','.txt?x=1'),job['body'].replace(A,A+'.evil.test'),
                     job['body']+' '+A+'/scenarios/control.txt'):
            with self.assertRaises(ValueError): validate_issue({**job,'body':body},cfg,42)
        with self.assertRaises(ValueError): validate_issue({**job,'number':43},cfg,42)

    def test_one_attempt_per_issue_and_persistent_daily_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'quota.sqlite'; reserve(db,1,2)
            with self.assertRaises(ValueError): reserve(db,1,2)
            reserve(db,2,2)
            with self.assertRaises(ValueError): reserve(db,3,2)

    def test_public_source_excludes_secrets_state_and_original_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            bad=['local/anthropic-api-key','docs/validation/runs/demo/trace.jsonl',
                 'infrastructure/aws/.build/deployment.json','infrastructure/aws/.terraform/provider',
                 'infrastructure/aws/terraform.tfvars.json','infrastructure/aws/terraform.tfstate',
                 '.github/workflows/investigate.yml','agent/config.json']
            good=['README.md','agent/run.py','infrastructure/aws/versions.tf','.github/workflows/aws-triage.yml']
            for name in bad+good:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            names={str(rel) for rel,_ in public_files(root)}
            self.assertEqual(names,set(good))

    def test_stable_issue_url_has_unique_per_run_followup(self):
        cfg={'diagnostic_origin':A,'receiver_origin':B,'diagnostic_path_template':'/scenarios/{variant}.txt'}
        one=urls(cfg,RUN,'injection');two=urls(cfg,'demo-'+'b'*32,'injection')
        self.assertEqual(one[0],two[0]);self.assertNotEqual(one[1],two[1])

    def test_content_length_eof_and_run_header_only_on_diagnostic(self):
        def response():
            raw=io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 3\r\n\r\nlog')
            raw._sock=MagicMock()
            sock=types.SimpleNamespace(makefile=lambda *args:io.BufferedReader(raw))
            result=http.client.HTTPResponse(sock);result.begin();return result
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'diagnostic_origin':A,'receiver_origin':B,'diagnostic_path_template':'/scenarios/{variant}.txt',
                 'send_run_header':True,'max_calls':6,'transport':'public-https','data_dir':tmp}
            with patch('agent.http_tool.public_addresses',return_value=[(2,1,6,'',('8.8.8.8',443))]), \
                 patch('agent.http_tool.PinnedHTTPS') as connection:
                connection.return_value.getresponse.side_effect=lambda:response()
                tool=LabHTTP(cfg,RUN,'injection')
                self.assertEqual(tool.fetch(tool.a)['body'],'log')
                a_headers=connection.return_value.request.call_args.kwargs['headers']
                self.assertEqual(a_headers['X-Lab-Run'],RUN)
                self.assertEqual(connection.return_value.request.call_count,1)
                self.assertEqual(tool.fetch(tool.b)['body'],'log')
                b_headers=connection.return_value.request.call_args.kwargs['headers']
                self.assertNotIn('X-Lab-Run',b_headers)


if __name__=='__main__': unittest.main()
