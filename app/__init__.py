"""
Ticket Bridge
=============

Serviço leve de correlação de tickets entre N aplicações de suporte,
desenhado para substituir a função de "hub central" que o OSTicket
desempenhava implicitamente.

Arquitetura (ver CLAUDE.md para o racional completo):
    - FastAPI stateless, corre em Cloud Run (escala a zero).
    - PostgreSQL (Cloud SQL) como única fonte de verdade e fila
      transacional (outbox pattern) - sem RabbitMQ/Pub-Sub.
    - Cloud Scheduler dispara periodicamente o endpoint de sync que
      processa a outbox e entrega eventos pendentes a cada sistema.
    - Suporta fan-out para N sistemas por conversa (não apenas um par A/B).
"""
