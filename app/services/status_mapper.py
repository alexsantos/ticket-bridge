"""
status_mapper.py
-----------------
Traduz estados entre o "vocabulário comum" interno (ex: 'novo',
'em_progresso', 'aguarda_terceiros', 'resolvido', 'fechado') e o
vocabulário específico de cada sistema externo (ex: 'Aberto' no sistema A,
'NEW' no sistema B).

O mapeamento fica configurado em `systems.status_mapping` (JSON:
vocabulário_interno -> vocabulário_externo). Isto evita hardcode de estados
por sistema no código - adicionar um sistema novo é só preencher esta
configuração.
"""
import logging

logger = logging.getLogger(__name__)

# Vocabulário interno "canónico". Sistemas novos devem mapear os seus
# próprios estados para um destes valores.
VOCABULARIO_INTERNO = {
    "novo",
    "em_progresso",
    "aguarda_terceiros",
    "resolvido",
    "fechado",
}


def externo_para_interno(status_mapping: dict[str, str], status_externo: str) -> str:
    """
    Converte um status no vocabulário de um sistema externo para o
    vocabulário interno. status_mapping é {interno: externo}, por isso
    invertemos a procura.

    Se não houver mapeamento conhecido, devolve o valor original em minúsculas
    e regista um aviso - preferimos não perder a informação a falhar.
    """
    invertido = {v: k for k, v in status_mapping.items()}
    if status_externo in invertido:
        return invertido[status_externo]

    logger.warning(
        "Status externo '%s' sem mapeamento conhecido; a usar valor literal.", status_externo
    )
    return status_externo.lower()


def interno_para_externo(status_mapping: dict[str, str], status_interno: str) -> str:
    """Converte um status do vocabulário interno para o vocabulário de um sistema de destino."""
    if status_interno in status_mapping:
        return status_mapping[status_interno]

    logger.warning(
        "Status interno '%s' sem mapeamento para este sistema; a usar valor literal.",
        status_interno,
    )
    return status_interno
