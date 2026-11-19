
import json, sys
from . import pipeline

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--verdicts" in argv:
        # Read-only review: adjudicate the stored records and print one
        # verdict per product, with candidates and basis.  No fetching.
        from .store import Store
        from . import kernel
        con = Store().connect()
        try:
            verdicts = kernel.adjudicate(Store(con).all_records())
        finally:
            con.close()
        print(json.dumps(verdicts, ensure_ascii=False, indent=2))
        return
    mode = "full"
    if "--incremental" in argv:
        mode = "incremental"
    if "--full" in argv:
        mode = "full"
    print(json.dumps(pipeline.run(mode=mode), ensure_ascii=False))

if __name__ == "__main__":
    main()
