import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from api.evaluation.argilla_client import _get_argilla_client, DATASET_NAME, WORKSPACE

client = _get_argilla_client()
dataset = client.datasets(name=DATASET_NAME, workspace=WORKSPACE)

for record in dataset.records(with_responses=True):
    status = getattr(record, "status", "pending")
    if str(status) == "completed":
        responses = getattr(record, "responses", None)
        print("dir(responses):", dir(responses))
        print("type(responses):", type(responses))
        
        # Test if it's iterable
        print("Is it iterable?")
        try:
            for x in responses:
                print(f"  Iterated item: type={type(x)}, dir={dir(x)}")
                # Try getting the question name
                print(f"  question_name: {getattr(x, 'question_name', getattr(x, 'name', 'N/A'))}")
                print(f"  value: {getattr(x, 'value', 'N/A')}")
        except Exception as e:
            print("  Not iterable:", e)
            
        print("dict(responses)?:")
        try:
            d = dict(responses)
            print(d)
        except Exception as e:
            print("  Failed:", e)
            
        print("responses.__dict__?:")
        try:
            print(responses.__dict__)
        except Exception as e:
            print("  Failed:", e)

        break
