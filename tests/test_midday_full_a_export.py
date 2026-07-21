from __future__ import annotations

from types import SimpleNamespace

from openpyxl import load_workbook

from midday.full_a_export import export_full_a_midday


def report():
    return {"run_id":"midday-full-a-test1234","trade_date":"2026-07-20","cutoff":"2026-07-20T11:30:00+08:00","status":"FULL_A_MIDDAY_EMPTY_BUY_READY","previous_regime":"REPAIR","midday_regime":"RISK_OFF","regime_reason":["test"],"counts":{"full_a_eligible_count":1200,"full_a_coverage":1.0,"radar_top200_count":200,"admission_distribution":{},"buy_ready":0,"afternoon_watch":0,"full_a_requested":1200,"full_a_returned":1200},"radar_top200":[{"midday_rank":1,"stock_code":"000001.SZ","stock_name":"平安银行","industry":"银行","baseline_quant_score":60,"midday_radar_score":70,"morning_relative_strength":70,"morning_volume_price":60,"sector_resonance":65,"opening_risk_quality":80,"change_pct_to_cutoff":.01,"amplitude_to_cutoff":.02,"industry_rank":1,"data_quality":"VALID_EXACT","risk_flags":[]}],"results":[],"breadth":{},"industries":[],"snapshot_semantic_validation":{"passed":True,"sample_count":20,"consistent_count":20},"asof_resolution_method":"HYBRID","provider_audit":[],"post_cutoff_rows_excluded":0,"radar_version":"MIDDAY_FULL_A_RADAR_V1","radar_weight_hash":"h","source_hashes_before":{},"source_hashes_after":{}}


def test_excel_has_exact_fifteen_sheets(tmp_path):
    paths=export_full_a_midday(tmp_path,SimpleNamespace(run_id="midday-full-a-test1234"),report());wb=load_workbook(paths["excel"])
    assert len(wb.sheetnames)==17 and wb.sheetnames[0]=="01_午盘总览"
    assert wb.sheetnames[-1]=="17_算法说明"
def test_excel_home_status_is_final(tmp_path):
    paths=export_full_a_midday(tmp_path,SimpleNamespace(run_id="midday-full-a-test1234"),report());wb=load_workbook(paths["excel"])
    assert wb["01_午盘总览"]["B5"].value=="FULL_A_MIDDAY_EMPTY_BUY_READY"
def test_excel_cells_are_centered(tmp_path):
    paths=export_full_a_midday(tmp_path,SimpleNamespace(run_id="midday-full-a-test1234"),report());wb=load_workbook(paths["excel"])
    assert all(cell.alignment.horizontal=="center" and cell.alignment.vertical=="center" for ws in wb for row in ws.iter_rows() for cell in row if cell.value is not None and cell.coordinate!="A1")
def test_excel_stock_code_is_text(tmp_path):
    paths=export_full_a_midday(tmp_path,SimpleNamespace(run_id="midday-full-a-test1234"),report());ws=load_workbook(paths["excel"])["02_全A雷达Top200"]
    assert ws["B4"].value=="000001.SZ" and ws["B4"].number_format=="@"
def test_excel_has_zero_formula_errors(tmp_path):
    paths=export_full_a_midday(tmp_path,SimpleNamespace(run_id="midday-full-a-test1234"),report());wb=load_workbook(paths["excel"],data_only=False)
    assert not [cell.value for ws in wb for row in ws.iter_rows() for cell in row if isinstance(cell.value,str) and cell.value.startswith("#")]
