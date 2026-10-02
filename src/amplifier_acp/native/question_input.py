"""One host-authorized answer, delivered through ordinary native execution."""
import hashlib
import json

async def admit(controls,runtime,args,activation=None,authorize=None,provenance_path=None):
    if authorize is None:return {'accepted':False,'executed':False,'reason':'Question authority unavailable'}
    for name in ('inputId','questionId'):
        if not isinstance(args.get(name),str) or not 1<=len(args[name])<=200:raise ValueError('Bounded question/input identity required')
    if type(args.get('questionRevision')) is not int or args['questionRevision']<0:raise ValueError('Question revision required')
    if not isinstance(args.get('text'),str) or not args['text'].strip() or len(args['text'])>runtime.max_input_chars:raise ValueError('Bounded answer text required')
    provenance=args.get('answerProvenance')
    if not isinstance(provenance,dict) or len(json.dumps(provenance,ensure_ascii=False,allow_nan=False).encode())>8192:raise ValueError('Bounded answer provenance required')
    try:controls.require_idle()
    except ValueError as exc:return {'accepted':False,'executed':False,'reason':str(exc)}
    proof=await authorize(args)
    required={name:args[name] for name in ('inputId','questionId','questionRevision')};required['answerHash']=hashlib.sha256(args['text'].encode()).hexdigest()
    if not isinstance(proof,dict) or proof.get('admitted') is not True or any(proof.get(k)!=v for k,v in required.items()):return {'accepted':False,'executed':False,'reason':'Saved question answer authority changed or was not proven'}
    from .input_provenance import reserve
    reserve(provenance_path,args['inputId'],{'inputOrigin':'question','questionId':args['questionId'],'questionRevision':args['questionRevision'],'answerProvenance':provenance})
    from amplifier_module_loop_live.runtime import Input
    identity=await runtime.submit(Input('user',args['text'],id=args['inputId'],activation=activation))
    return {'accepted':True,'inputId':identity}
