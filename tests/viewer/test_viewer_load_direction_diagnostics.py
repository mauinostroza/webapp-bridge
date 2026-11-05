# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from types import SimpleNamespace
import pytest
from bridge.viewer.core.load_direction import load_direction_vector
from bridge.viewer.sap2000.reader_geometry import _frame_point_loads, _parse_joint_force_result
from bridge.viewer.sap2000.model_reader import SapModelReader
from bridge.viewer.sap2000.fake_model import FakeSapObject


def test_sap_local_axes_and_rotation():
    assert load_direction_vector(2,(1,0,0),0,'Local')==pytest.approx((0,0,1))
    assert load_direction_vector(3,(1,0,0),0,'Local')==pytest.approx((0,-1,0))
    assert load_direction_vector(2,(1,0,0),90,'Local')==pytest.approx((0,-1,0))
    assert load_direction_vector(2,(0,0,1),0,'Local')==pytest.approx((1,0,0))
    v=load_direction_vector(2,(1,2,3),0,'Local')
    assert sum(a*b for a,b in zip(v,(1,2,3)))==pytest.approx(0)
    assert v[2]>0


@pytest.mark.parametrize('code,system', [('unknown','Global'),('nan','Global'),(1.5,'Global'),(12,'Global'),(4,'ROTATED'),(2,'Global'),(6,'Local')])
def test_uninterpretable_direction_rejected(code,system):
    with pytest.raises(ValueError):
        load_direction_vector(code,(1,0,0),0,system)


def test_unknown_rotation_rejected_and_projected_direction_supported():
    with pytest.raises(ValueError):
        load_direction_vector(2,(1,0,0),None,'Local')
    assert load_direction_vector('Global Z Projected')==(0,0,1)


def test_nonzero_return_not_zero_loads():
    errors=[]
    obj=SimpleNamespace(GetLoadPoint=lambda *a:(['F'],['D'],[1],[6],[.5],[10],9))
    assert _frame_point_loads(obj,'F',errors)==[]
    assert 'SAP return=9' in errors[0]


def test_zero_assignments_success_has_no_warning():
    errors=[]
    assert _frame_point_loads(SimpleNamespace(GetLoadPoint=lambda *a:(0,[],[],[],[],[],[],0)),'F',errors)==[]
    assert errors==[]


def test_lcstep_not_interpreted_as_force():
    raw=(1,['J'],['D'],[2],['Global'],[0.],[0.],[-10.],[0.],[0.],[0.],0)
    assert _parse_joint_force_result('J',raw)[0]=={'joint':'J','pattern':'D','forces':[0.,0.,-10.,0.,0.,0.],'csys':'Global'}


def test_object_error_metadata_and_summary():
    model=FakeSapObject().SapModel
    model.DatabaseTables=None
    model.FrameObj.GetLoadPoint=lambda *a:(_ for _ in ()).throw(RuntimeError('disconnected'))
    table=SapModelReader(model).frame_point_loads_list(frame_name='F1')
    assert table['rows']==[]
    assert 'disconnected' in table['metadata']['errors'][0]
    assert 'incompleta' in table['summary_message']


def test_real_csys_appended_without_reordering_legacy_values():
    model=FakeSapObject().SapModel
    model.DatabaseTables=None
    model.FrameObj.GetLoadPoint=lambda name,*a:(1,[name],['D'],[1],['ROTATED'],[6],[.5],[3.],[10.],0)
    table=SapModelReader(model).frame_point_loads_list(frame_name='F1')
    assert table['columns'][-1]=='CSys'
    assert table['rows'][0]==['F1','D','Force',6,.5,10.,'ROTATED']
    assert table['rows_flat']==['10.0']


def test_explicit_byref_signature_supported_without_warning():
    calls=[]
    def getter(*args):
        calls.append(len(args))
        if len(args)!=11:raise TypeError('ByRef required')
        return (1,['F'],['D'],[1],['Global'],[6],[.5],[3.],[10.],0)
    errors=[]
    result=_frame_point_loads(SimpleNamespace(GetLoadPoint=getter),'F',errors)
    assert result[0]['csys']=='Global' and errors==[]
    assert calls==[1,2,11]


def test_malformed_array_lengths_reported():
    errors=[]
    obj=SimpleNamespace(GetLoadPoint=lambda *a:(['F'],['D'],[1],[6],0))
    assert _frame_point_loads(obj,'F',errors)==[]
    assert 'incompletos' in errors[0]


def test_absolute_batch_rejected_to_relative_api(monkeypatch):
    import bridge.viewer.sap2000.reader_geometry as geometry
    table=(["Frame","LoadPat","Type","Dir","Dist","Val"],[["F","D","Force",6,3.,10.]])
    monkeypatch.setattr(geometry,'_database_geometry_tables',lambda *a:[table])
    assert geometry._frame_point_loads_from_database(object(),[]) is None
    table[0][4]='RelDist'
    assert geometry._frame_point_loads_from_database(object(),[]) is not None


def test_scalar_api_error_preserves_return_code():
    errors=[]
    assert _frame_point_loads(SimpleNamespace(GetLoadPoint=lambda *a:7),'F',errors)==[]
    assert 'SAP return=7' in errors[0]


