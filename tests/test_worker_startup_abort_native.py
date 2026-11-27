"""Shutdown and partial-init failures must retain child ownership."""
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistx import worker as w, worker_supervision as s

class StartupAbortTests(unittest.TestCase):
    def test_stop_during_validation_prevents_warmup_and_group_launch(self):
        with patch.object(w,'validate_runtime_configuration',side_effect=lambda **kw:signal.raise_signal(signal.SIGTERM)), patch.object(w,'_warm_parent') as warm, patch.object(w,'_run_worker_group') as group:
            w.main()
            warm.assert_not_called()
            group.assert_not_called()

    def test_stop_during_warmup_prevents_group_launch(self):
        with patch.object(w,'validate_runtime_configuration'),patch.object(w,'_warm_parent',side_effect=lambda stopping:signal.raise_signal(signal.SIGTERM)),patch.object(w,'_run_worker_group') as group:
            w.main()
            group.assert_not_called()

    def test_warmup_checks_stop_between_imports(self):
        state=[False]; calls=[]
        def load(name):
            calls.append(name);state[0]=True
        with patch('importlib.import_module',side_effect=load):
            w._warm_parent(lambda:state[0])
        self.assertEqual(calls,['assistx.jobs'])

    def exercise_failed_group(self,where):
        child=Mock();child.is_alive.return_value=True
        ctx=Mock()
        reader,writer=Mock(),Mock();ctx.Pipe.return_value=(reader,writer)
        ctx.Event.return_value=Mock()
        ctx.Event.return_value.is_set.return_value=False
        owned=[]
        def supervise(processes,stopping,**kw):
            owned.extend(processes)
            self.assertTrue(kw['faulted']())
            kw['on_shutdown']()
            return 70
        pollers=Mock(return_value=[])
        ready=Mock(return_value=None)
        if where=='readiness':ready.side_effect=OSError('synthetic')
        if where=='poller':pollers.side_effect=RuntimeError('synthetic')
        if where=='readiness_value':ready.side_effect=ValueError('synthetic')
        if where=='poller_value':pollers.side_effect=ValueError('synthetic')
        if where=='pipe_close':writer.close.side_effect=OSError('synthetic')
        with patch.object(w.mp,'get_context',return_value=ctx),patch.object(w,'settings',SimpleNamespace(execution_backend='direct')),patch.dict('os.environ',{'WORKER_CONCURRENCY':'1'}),patch.object(s,'launch_children',return_value=([child],None)),patch.object(s,'wait_child_readiness',ready),patch.object(s,'supervise',side_effect=supervise),patch.object(w,'_start_execution_pollers',pollers),patch.object(w,'_start_recovery_island_controller') as recovery:
            self.assertEqual(w._run_worker_group(lambda:False),70)
            self.assertEqual(owned,[child])
            recovery.assert_not_called()
            reader.close.assert_called()
            writer.close.assert_called()

    def test_readiness_exception_drains_owned_children(self):self.exercise_failed_group('readiness')
    def test_poller_exception_drains_owned_children(self):self.exercise_failed_group('poller')

    def test_readiness_value_error_drains_children(self):self.exercise_failed_group('readiness_value')
    def test_poller_value_error_drains_children(self):self.exercise_failed_group('poller_value')
    def test_pipe_close_error_drains_children(self):self.exercise_failed_group('pipe_close')

    def test_validation_failure_is_not_success(self):
        with patch.object(w,'validate_runtime_configuration',side_effect=ValueError('synthetic')),patch.object(w,'_warm_parent') as warm,patch.object(w,'_run_worker_group') as group:
            with self.assertRaises(ValueError):w.main()
            warm.assert_not_called();group.assert_not_called()

    def test_parent_exit_code_preserved(self):
        with patch.object(w,'validate_runtime_configuration'),patch.object(w,'_warm_parent'),patch.object(w,'_run_worker_group',return_value=70):
            with self.assertRaises(SystemExit) as e:w.main()
            self.assertEqual(e.exception.code,70)
