"""Build a reviewed editor expression from explicit UTF-8 JSON options."""
from pathlib import Path
import argparse
import json

ROOT=Path(__file__).resolve().parent/'editor'
KINDS={'codebox-image':'replace_codebox.js','native-math':'replace_math.js',
       'copy-math':'copy_math.js','delete-text':'delete_text.js',
       'format':'format.js','insert-table':'insert_table.js','codeblock':'codeblock.js'}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',required=True,choices=KINDS)
    parser.add_argument('--options',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    options=json.loads(args.options.read_text(encoding='utf-8'))
    if not isinstance(options,dict):parser.error('Options must be a JSON object')
    if args.kind in ('format','insert-table','codeblock'):
        from format_options import validate
        try:
            validate(args.kind, options)
        except ValueError as error:
            parser.error(str(error))
    source=(ROOT/KINDS[args.kind]).read_text(encoding='utf-8')
    expression='('+source+')('+json.dumps(options,ensure_ascii=False)+')\n'
    if len(expression.encode('utf-16-le'))//2>16384:
        parser.error('Expression exceeds the checked CEF limit; handle one explicit fragment at a time')
    with args.output.open('x',encoding='utf-8') as stream:
        stream.write(expression)
    print(json.dumps({'kind':args.kind,'script':str(args.output.resolve()),'utf16_units':len(expression.encode('utf-16-le'))//2}))


if __name__=='__main__':main()
