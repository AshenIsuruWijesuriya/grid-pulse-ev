"""Quality annotations must survive multi-month consolidation."""
import csv
from scripts.consolidate_pucsl_dispatch import consolidate
from test_pucsl_consolidation import make_month, write_csv


def test_quality_annotations_are_preserved_and_counted(tmp_path):
    path = make_month(tmp_path, year=2024, month=4)
    with path.open(newline='', encoding='utf-8') as f:
        reader=csv.DictReader(f)
        rows=list(reader)
        columns=list(reader.fieldnames)
    columns += ['interval_quality_status','interval_quality_flags']
    for row in rows:
        row['interval_quality_status']='no_interval_flags'
        row['interval_quality_flags']=''
    rows[0]['interval_quality_status']='review_required'
    rows[0]['interval_quality_flags']='source_balance_exception'
    write_csv(path, rows, columns)
    combined, days, report = consolidate(tmp_path, 2024, [4], include_partial=False)
    assert len(combined)==2880
    assert combined[0]['interval_quality_flags']=='source_balance_exception'
    assert report['months'][0]['intervals_requiring_quality_review']==1
    assert report['months'][0]['interval_quality_annotation_available'] is True


def test_legacy_month_does_not_claim_zero_quality_issues(tmp_path):
    make_month(tmp_path,year=2024,month=4)
    _,_,report=consolidate(tmp_path,2024,[4],False)
    assert report['months'][0]['interval_quality_annotation_available'] is False
    assert report['months'][0]['intervals_requiring_quality_review'] is None
