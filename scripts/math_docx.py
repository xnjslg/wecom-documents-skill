"""Convert requested LaTeX expressions into native Office Math source samples."""
from pathlib import Path
import argparse
import hashlib
import json
import os

def office_xsl():
    """Discover Office's installed transform, without copying its licensed asset."""
    configured = os.environ.get('WECOM_MATH_XSL')
    if configured:
        return Path(configured)
    for key in ('ProgramFiles', 'ProgramFiles(x86)'):
        base = os.environ.get(key)
        if not base:
            continue
        for relative in ('Microsoft Office/root/Office16/MML2OMML.XSL',
                         'Microsoft Office/Office16/MML2OMML.XSL'):
            candidate = Path(base) / relative
            if candidate.is_file():
                return candidate
    return None


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path,help='JSON list of {latex: string, display: boolean}')
    parser.add_argument('--output',required=True,type=Path,help='New DOCX path used as a separate formula source')
    parser.add_argument('--xsl',type=Path,default=office_xsl())
    args=parser.parse_args()
    equations=json.loads(args.input.read_text(encoding='utf-8'))
    if not isinstance(equations,list) or not equations:
        parser.error('Input must contain at least one actual expression')
    for value in equations:
        if not isinstance(value,dict) or not isinstance(value.get('latex'),str) or not value['latex'].strip() or type(value.get('display'))is not bool:
            parser.error('Each item requires nonempty latex and an explicit display boolean')
    if args.output.exists():parser.error('Output exists; choose a new formula source file')
    if args.xsl is None or not args.xsl.is_file():parser.error('Provide the installed Microsoft MML2OMML.XSL with --xsl or WECOM_MATH_XSL')
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from lxml import etree
    from latex2mathml.converter import convert
    transform=etree.XSLT(etree.parse(str(args.xsl)))
    document=Document()
    for equation in equations:
        mathml=convert(equation['latex'])
        omml=transform(etree.fromstring(mathml.encode())).getroot()
        if etree.QName(omml).localname!='oMath':raise ValueError('Expected native Office Math output')
        paragraph=document.add_paragraph()
        if equation['display']:
            wrapper=OxmlElement('m:oMathPara');properties=OxmlElement('m:oMathParaPr')
            alignment=OxmlElement('m:jc');alignment.set(qn('m:val'),'center');properties.append(alignment)
            wrapper.append(properties);wrapper.append(omml);paragraph._p.append(wrapper)
        else:paragraph._p.append(omml)
    document.save(args.output)
    print(json.dumps({'docx':str(args.output.resolve()),'equations':len(equations),
                      'bytes':args.output.stat().st_size,'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__=='__main__':main()
