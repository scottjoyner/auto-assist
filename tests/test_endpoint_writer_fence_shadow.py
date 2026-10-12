"""Offline contract checks for UNWIRED synthetic endpoint writer fence shadow."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[1]/"src/assistx/endpoint_writer_fence_shadow.py"
spec=importlib.util.spec_from_file_location("shadow_fence",SOURCE)
assert spec and spec.loader
from sys import modules
mod=importlib.util.module_from_spec(spec)
modules[spec.name]=mod
spec.loader.exec_module(mod)

ROOT="scratch-acceptance-"
NODE=ROOT+"node-1"
OLD=ROOT+"node-1:generic"
NEW=ROOT+"ordinary-service-new"
MODEL=ROOT+"old-model"
PFX=ROOT+"other"

def policy(*, ids=None, approved=True, inventory=True, drained=True, rehearsal=True, gen=5):
 return mod.SyntheticFence(
     blocked_ids=frozenset(ids if ids is not None else (OLD,MODEL)),
     issuer_policy_approved=approved,producer_inventory_verified=inventory,
     queues_drained=drained,synthetic_rehearsal=rehearsal,generation=gen,
 )

def node_service(endpoint=OLD):
 return {"node_id":NODE,"services":[{"endpoint_id":endpoint,"service_type":"generic"}]}

def evt(kind, payload):
 return {"event_type":kind,"node_id":NODE,"payload":payload,
         "subject":{"kind":"synthetic","id":ROOT+"subject"}}

class FenceShadowTests(unittest.TestCase):
 def assertDenied(self, result):
  self.assertFalse(result.would_invoke_next_stage)
  self.assertFalse(result.production_release_authorized)
  self.assertTrue(result.status.startswith(("DENY_","HOLD_")),result.status)

 def test_direct_node_registration_denies_fenced_id(self):
  self.assertEqual(mod.check("node_register",node_service(),policy()).status,
                   "DENY_SYNTHETIC_FENCED_ENDPOINT")

 def test_node_heartbeat_denies_fenced_service_id(self):
  r=mod.check("node_heartbeat",{"services":[{"endpoint_id":OLD}]},policy(),
              route_node_id=NODE)
  self.assertDenied(r)

 def test_node_default_endpoint_id_is_denied_too(self):
  r=mod.check("node_register",{"node_id":NODE,
           "services":[{"service_type":"generic"}]},policy())
  self.assertDenied(r)

 def test_direct_model_registration_denies_fenced_id(self):
  r=mod.check("model_register",{"model_endpoint_id":MODEL,"node_id":NODE},policy())
  self.assertDenied(r)

 def test_model_probe_and_delete_route_ids_cannot_bypass(self):
  for operation in ("model_probe","model_delete"):
   with self.subTest(operation=operation):
    self.assertDenied(mod.check(operation,{},policy(),route_endpoint_id=MODEL))

 def test_event_node_registered_and_heartbeat_denied(self):
  for event in ("swarm.node.registered","swarm.node.heartbeat"):
   with self.subTest(event=event):
    self.assertDenied(mod.check("event_ingest",evt(event,node_service()),policy()))

 def test_discovery_event_denied(self):
  e=evt("model.endpoint.discovered",{"model_endpoint_id":MODEL,"node_id":NODE})
  self.assertDenied(mod.check("event_ingest",e,policy()))

 def test_retry_in_other_event_with_stale_reference_denied(self):
  e=evt("route.selected",{"extra":[{"endpoint_id":OLD}]})
  self.assertEqual(mod.check("event_ingest",e,policy()).status,
                   "DENY_STALE_REFERENCE_IN_OTHER_EVENT")

 def test_unrelated_heartbeat_with_no_services_would_pass(self):
  e=evt("swarm.node.heartbeat",{"node_id":NODE,"services":[]})
  result=mod.check("event_ingest",e,policy())
  self.assertTrue(result.would_invoke_next_stage)
  self.assertFalse(result.production_release_authorized)

 def test_legitimate_distinct_service_allowed_review_only(self):
  result=mod.check("node_register",node_service(NEW),policy())
  self.assertTrue(result.would_invoke_next_stage)
  self.assertFalse(result.production_release_authorized)
  self.assertIn("REVIEW_ONLY",result.status)

 def test_unknown_ingress_path_holds(self):
  self.assertDenied(mod.check("other_route",node_service(NEW),policy()))

 def test_unapproved_or_undrained_policy_holds(self):
  cases=[
      policy(approved=False),policy(inventory=False),policy(drained=False),
      policy(rehearsal=False),policy(gen=0),policy(ids=()),
  ]
  for entry in cases:
   with self.subTest(entry=entry):
    self.assertDenied(mod.check("node_register",node_service(NEW),entry))

 def test_real_endpoint_ids_fail_closed(self):
  self.assertDenied(mod.check("model_register",{"model_endpoint_id":"real-prod-id"},
                              policy()))
  self.assertDenied(mod.check("model_register",{"model_endpoint_id":MODEL},
                              policy(ids=("real-prod-id",))))

 def test_multiple_identifiers_must_agree(self):
  r=mod.check("model_register",{"model_endpoint_id":NEW,"endpoint_id":OLD},policy())
  self.assertDenied(r)
  self.assertEqual(r.status,"HOLD_CONFLICTING_ENDPOINT_IDS")

 def test_heartbeat_path_vs_payload_mismatch_holds(self):
  r=mod.check("node_heartbeat",{"node_id":ROOT+"other-node","services":[]},
              policy(),route_node_id=NODE)
  self.assertDenied(r)

 def test_service_list_shape_and_nested_model_conflict_hold(self):
  r=mod.check("node_register",{"node_id":NODE,"services":"not-a-list"},policy())
  self.assertDenied(r)
  r=mod.check("node_register",{"node_id":NODE,"services":[
      {"endpoint_id":NEW,"model_endpoint_id":MODEL}]},policy())
  self.assertDenied(r)

 def test_missing_event_type_or_model_id_holds(self):
  self.assertDenied(mod.check("event_ingest",{"payload":{}},policy()))
  self.assertDenied(mod.check("model_register",{"node_id":NODE},policy()))

 def test_unknown_nested_reference_depth_holds(self):
  value={"endpoint_id":NEW}
  for _ in range(9):value={"child":[value]}
  self.assertDenied(mod.check("event_ingest",evt("route.selected",value),policy()))

 def test_blocked_callbacks_are_never_called(self):
  calls=[]
  deny=mod.guarded_synthetic_call("node_register",node_service(),policy(),
                                lambda:calls.append("sent"))
  self.assertDenied(deny)
  self.assertEqual(calls,[])

 def test_unapproved_callbacks_are_never_called(self):
  calls=[]
  deny=mod.guarded_synthetic_call("node_register",node_service(NEW),
                                  policy(approved=False),lambda:calls.append("sent"))
  self.assertDenied(deny)
  self.assertEqual(calls,[])

 def test_review_only_mock_callback_executes_with_explicit_all_green_policy(self):
  calls=[]
  accepted=mod.guarded_synthetic_call("node_register",node_service(NEW),policy(),
                                      lambda:calls.append("synthetic-called"))
  self.assertEqual(calls,["synthetic-called"])
  self.assertFalse(accepted.production_release_authorized)

 def test_no_runtime_or_db_imports_and_no_network_migrations(self):
  content=SOURCE.read_text()
  for forbidden in ("neo4j","FastAPI","requests.","docker run","MATCH (",
                    "MERGE (", "DETACH DELETE","os.environ"):
   self.assertNotIn(forbidden,content)
  self.assertIn("synthetic_rehearsal",content)

if __name__=="__main__":unittest.main(verbosity=1)
