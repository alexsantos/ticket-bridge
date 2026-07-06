"""
test_status_mapper.py
----------------------
Testes unitários puros (sem base de dados) da tradução de vocabulários de
estado. Ponto de partida para a suite de testes - correr com:

    pytest tests/test_status_mapper.py -v
"""
from app.services.status_mapper import externo_para_interno, interno_para_externo

MAPPING_EXEMPLO = {
    "novo": "Aberto",
    "em_progresso": "Em Curso",
    "resolvido": "Resolvido",
}


def test_interno_para_externo_conhecido():
    assert interno_para_externo(MAPPING_EXEMPLO, "novo") == "Aberto"


def test_interno_para_externo_desconhecido_devolve_literal():
    assert interno_para_externo(MAPPING_EXEMPLO, "estado_nao_mapeado") == "estado_nao_mapeado"


def test_externo_para_interno_conhecido():
    assert externo_para_interno(MAPPING_EXEMPLO, "Em Curso") == "em_progresso"


def test_externo_para_interno_desconhecido_devolve_minusculas():
    assert externo_para_interno(MAPPING_EXEMPLO, "EstadoQualquer") == "estadoqualquer"
