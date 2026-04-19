import os
import logging
import sys

# Add current dir to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from argilla_client import _get_argilla_client, DATASET_NAME, WORKSPACE
except ImportError:
    # Fallback if imports fail
    def _get_argilla_client():
        import argilla as rg
        return rg.Argilla(api_url="http://argilla:6900", api_key="argilla.apikey")
    DATASET_NAME = "clinical-trial-eval"
    WORKSPACE = "argilla"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def debug_sync():
    client = _get_argilla_client()
    if not client:
        print("❌ Could not connect to Argilla")
        return

    try:
        dataset = client.datasets(name=DATASET_NAME, workspace=WORKSPACE)
        if not dataset:
            print(f"❌ Dataset {DATASET_NAME} not found in workspace {WORKSPACE}")
            return

        print(f"✅ Connected to dataset: {DATASET_NAME} (ID: {dataset.id})")
        
        # Check Fields
        print("\n📋 Dataset Fields:")
        for field in dataset.fields:
            print(f"  - {field.name}")

        # Check Questions
        print("\n📋 Dataset Questions:")
        for question in dataset.questions:
            print(f"  - {question.name}")

        # Check total count
        try:
            print(f"📊 Total records in dataset property: {len(dataset.records)}")
        except: pass

        # Main fetch
        print("\n🔍 Attempting to fetch all records (with_responses=True)...")
        records = list(dataset.records(with_responses=True, with_suggestions=True))
        print(f"📥 Retrieved {len(records)} records")

        status_counts = {}
        with_responses = 0
        for r in records:
            s = getattr(r, "status", "pending")
            status_counts[s] = status_counts.get(s, 0) + 1
            if r.responses:
                with_responses += 1
        
        print(f"📊 Status breakdown: {status_counts}")
        print(f"💬 Records with non-empty responses: {with_responses}")

        for i, record in enumerate(records):
            # Only show first 5 OR any record that actually has responses
            if i < 5 or record.responses:
                print(f"\n--- [Record {i+1}] (ID: {record.id}) ---")
                print(f"Status: {getattr(record, 'status', 'pending')}")
                print(f"Metadata: {record.metadata}")
                
                responses = getattr(record, "responses", {})
                values = {}
                is_question_centric = False
                
                if hasattr(responses, "items"):
                    try:
                        for q_name, q_resps in responses.items():
                            if q_resps and isinstance(q_resps, (list, tuple)) and len(q_resps) > 0:
                                is_question_centric = True
                                item = q_resps[0]
                                val = None
                                if hasattr(item, "value"):
                                    val = item.value
                                elif isinstance(item, dict) and "value" in item:
                                    val = item["value"]
                                
                                if val is not None:
                                    values[q_name] = val
                    except Exception as e:
                        print(f"  ❌ Error parsing responses: {e}")
                
                print(f"Extracted Values: {values}")
                
                expected = values.get("expected_answer")
                correctness = values.get("correctness")
                should_export = expected or (correctness and correctness >= 4)
                print(f"Should Export: {should_export}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_sync()
