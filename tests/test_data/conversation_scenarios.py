

SERVICE_SCENARIOS = [
    {
        "name": "lead_servico_inicial",
        "contact_id": "5511999990001",
        "contact_name": "Ana",
        "messages": [
            "Olá, queria entender melhor como funciona o serviço de vocês.",
            "Vocês atendem online também?",
            "Queria ter uma noção de valores antes de decidir.",
            "Gostei e quero seguir, qual o próximo passo para contratar?",
        ],
    },
    {
        "name": "servico_com_agendamento",
        "contact_id": "5511999990002",
        "contact_name": "Bruno",
        "messages": [
            "Oi, queria agendar uma consultoria inicial.",
            "Tem horário disponível para amanhã de manhã?",
            "Posso escolher o profissional que vai me atender?",
            "Perfeito, então me explica como faço para confirmar esse agendamento.",
        ],
    },
]


PRODUCT_SCENARIOS = [
    {
        "name": "produto_digital_venda",
        "contact_id": "5511999990003",
        "contact_name": "Carla",
        "messages": [
            "Olá, queria saber mais sobre o material digital complementar.",
            "Esse produto está disponível para compra imediata?",
            "Qual é o valor dele e como funciona o pagamento?",
            "Certo, gostei. Como faço para comprar?",
        ],
    },
    {
        "name": "produto_com_duvida_comercial",
        "contact_id": "5511999990004",
        "contact_name": "Diego",
        "messages": [
            "Oi, vocês trabalham com produtos além dos serviços?",
            "Queria entender qual item vocês indicam para o meu caso.",
            "Também queria saber se tem diferença de preço entre as opções.",
            "Se fizer sentido para mim, eu posso fechar hoje mesmo.",
        ],
    },
]


RENTAL_SCENARIOS = [
    {
        "name": "locacao_com_prova",
        "contact_id": "5511999990005",
        "contact_name": "Elisa",
        "messages": [
            "Olá, tenho interesse na peça premium para locação.",
            "Ela está disponível para a próxima semana?",
            "Eu consigo agendar uma prova antes de decidir?",
            "Se eu gostar, como funciona para reservar a locação?",
        ],
    },
    {
        "name": "item_com_aluguel_ou_venda",
        "contact_id": "5511999990006",
        "contact_name": "Felipe",
        "messages": [
            "Oi, essa peça premium é só para aluguel ou vocês também vendem?",
            "Queria entender a diferença entre alugar e comprar.",
            "No caso de aluguel, como funciona a devolução?",
            "E se eu quiser seguir com a locação, qual seria o próximo passo?",
        ],
    },
]


SCHEDULING_SCENARIOS = [
    {
        "name": "agendamento_com_escolha_profissional",
        "contact_id": "5511999990007",
        "contact_name": "Gabriela",
        "messages": [
            "Olá, queria marcar um atendimento com a Ana.",
            "Ela tem disponibilidade na sexta à tarde?",
            "Se não tiver, vocês conseguem me indicar outro profissional com perfil parecido?",
            "Perfeito, então quero confirmar o horário disponível.",
        ],
    },
    {
        "name": "agendamento_sem_profissional_definido",
        "contact_id": "5511999990008",
        "contact_name": "Henrique",
        "messages": [
            "Oi, queria agendar um atendimento mas ainda não sei qual profissional escolher.",
            "Vocês podem me orientar sobre quem seria mais indicado?",
            "Também queria entender o valor e a duração desse atendimento.",
            "Se fizer sentido, eu já deixo meu agendamento encaminhado.",
        ],
    },
]


HYBRID_BUSINESS_SCENARIOS = [
    {
        "name": "empresa_hibrida_servico_produto",
        "contact_id": "5511999990009",
        "contact_name": "Isabela",
        "messages": [
            "Olá, primeiro queria entender como funciona a consultoria inicial.",
            "Depois queria saber se vocês também têm algum material complementar para comprar.",
            "Se eu fechar a consultoria, esse material pode ser incluído depois?",
            "Quero seguir, então me orienta sobre a melhor forma de começar.",
        ],
    },
    {
        "name": "empresa_hibrida_com_recorrencia",
        "contact_id": "5511999990010",
        "contact_name": "João",
        "messages": [
            "Oi, queria entender se vocês trabalham com atendimento pontual e também com plano mensal.",
            "Qual seria a diferença prática entre a consultoria inicial e o plano mensal?",
            "Se eu começar com algo menor, depois consigo migrar para um plano recorrente?",
            "Gostei da ideia. Qual é o melhor caminho para começar agora?",
        ],
    },
]


NON_COMMERCIAL_SCENARIOS = [
    {
        "name": "suporte_tecnico_basico",
        "contact_id": "5511999990011",
        "contact_name": "Karen",
        "messages": [
            "Oi, estou com problema no acesso da plataforma.",
            "Quando tento entrar, aparece erro e não consigo usar.",
        ],
    },
    {
        "name": "assunto_financeiro",
        "contact_id": "5511999990012",
        "contact_name": "Lucas",
        "messages": [
            "Olá, preciso tratar um pagamento pendente da nota fiscal.",
            "Queria confirmar com quem posso falar sobre isso.",
        ],
    },
]


ALL_SCENARIOS = (
    SERVICE_SCENARIOS
    + PRODUCT_SCENARIOS
    + RENTAL_SCENARIOS
    + SCHEDULING_SCENARIOS
    + HYBRID_BUSINESS_SCENARIOS
    + NON_COMMERCIAL_SCENARIOS
)