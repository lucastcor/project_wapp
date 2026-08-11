"""Automation Studio: domínio puro dos fluxos visuais.

Este pacote não faz I/O. Ele define o grafo, o catálogo de nós, a validação e
a **semântica única** que simulação e runtime compartilham — a separação que
impede que o que o operador vê na simulação divirja do que a execução faz.

Persistência, efeitos externos e agendamento vivem em `app/storage` e
`app/services`.
"""
