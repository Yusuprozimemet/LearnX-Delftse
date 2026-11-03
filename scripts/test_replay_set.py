import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'kit/replay/replay-set.py'


@unittest.skipUnless(SCRIPT.exists(), 'kit/ removed')
class ReplayTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('replay', SCRIPT)
        self.replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.replay)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        (self.base / 'keys').mkdir()
        self.key = [dict(id=str(i), statement='defect', severity=s, full='exact cause', partial='symptom')
                    for i, s in enumerate(('major', 'minor', 'blocking'))]
        self.raw = json.dumps(self.key).encode()
        (self.base / 'keys/dev.json').write_bytes(self.raw)
        (self.base / 'held.json').write_bytes(self.raw)
        self.case = dict(id='dev', kind='historical-defect', split='development', source=dict(repo='example', commit='a'*40),
                         spec='specs/day.md', task='audit', origin='fix PR', key='keys/dev.json',
                         verified=dict(by='maintainer', date='2026-10-30'))
        self.cases = [self.case]
        self.row = dict(output='saved.txt', minutes=12, tokens=100, review_minutes=3,
                        findings={'0': 'full', '1': 'partial', '2': 'missed'}, extras=[
                            dict(judgment=j, severity='major', evidence='saved.txt:1')
                            for j in ('false-alarm', 'valid-outside-key', 'duplicate')])

    def run_cli(self, command='check'):
        manifest = self.base / 'manifest.json'
        manifest.write_bytes(json.dumps(dict(version=1, cases=self.cases)).encode())
        args = [command, '--manifest', str(manifest)]
        if command == 'score':
            run = self.base / 'run.json'
            run.write_bytes(json.dumps(dict(kit_commit='b'*40, auditor_sha256='c'*64, model='model',
                                           cases={self.case['id']: self.row})).encode())
            args += [str(run), '--keys', str(self.base)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = self.replay.main(args)
        return status, out.getvalue()

    def refused(self, command='check'):
        status, output = self.run_cli(command)
        self.assertEqual(status, 1, output)
        self.assertIn(self.case['id'], output)

    def held(self):
        self.case.update(id='held', split='held-out', key=dict(sha256=hashlib.sha256(self.raw).hexdigest()))

    def test_empty(self):
        self.cases.clear()
        self.assertEqual(self.run_cli()[0], 0)
        self.assertIn('no cases', self.run_cli()[1])

    def test_committed_manifest(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = self.replay.main(['check'])
        output = out.getvalue()
        self.assertEqual(status, 0, output)
        self.assertIn('16 cases\n', output)
        self.assertIn('development=8 held-out=8 historical-defect=5 broken-check=4 cross-day=3 clean=4\n', output)

    def test_splits(self):
        dev = dict(self.case)
        self.held()
        self.cases.append(dev)
        status, output = self.run_cli()
        self.assertEqual(status, 0, output)
        self.assertIn('development=1 held-out=1', output)
        self.assertEqual(self.run_cli('score')[0], 0)

    def test_duplicate(self):
        self.cases.append(dict(self.case)); self.refused()

    def test_kind(self):
        self.case['kind'] = 'bad'; self.refused()

    def test_split(self):
        self.case['split'] = 'bad'; self.refused()

    def test_commit(self):
        self.case['source']['commit'] = 'abc'; self.refused()

    def test_missing_key(self):
        (self.base / 'keys/dev.json').unlink(); self.refused()

    def test_held_path(self):
        self.held(); self.case['key'] = dict(path='private.json'); self.refused()

    def test_arithmetic(self):
        status, output = self.run_cli('score')
        self.assertEqual(status, 0, output)
        for line in ('full=1 partial=1 missed=1', 'strict recall=0.333333 lenient recall=0.666667',
                     '\n  missed blocking=1\n', 'false alarms=1 precision=0.666667', 'valid-outside-key=1'):
            self.assertEqual(output.count(line), 2, output)

    def test_clean(self):
        self.case['kind'] = 'clean'
        (self.base / 'keys/dev.json').write_bytes(b'[]')
        self.row.update(findings={}, extras=self.row['extras'][:1])
        status, output = self.run_cli('score')
        self.assertEqual(status, 0, output)
        self.assertEqual(next(l.strip() for l in output.splitlines() if 'false alarms' in l), 'false alarms=1 precision=0.000000')
        self.assertIn('strict recall=n/a lenient recall=n/a', output)

    def test_unverified(self):
        self.case['verified'] = None; self.refused('score')

    def test_hash(self):
        self.held(); (self.base / 'held.json').write_bytes(self.raw + b'\n'); self.refused('score')

    def test_unjudged(self):
        del self.row['findings']['2']; self.refused('score')

    def test_unknown(self):
        self.row['findings']['other'] = 'full'; self.refused('score')

    def test_invalid_key(self):
        self.key[0]['severity'] = 'bad'
        (self.base / 'keys/dev.json').write_bytes(json.dumps(self.key).encode()); self.refused()


if __name__ == "__main__":
    unittest.main()
