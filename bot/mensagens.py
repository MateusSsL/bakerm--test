BOAS_VINDAS = (
    "🎉 Bem-vindo ao Cadastro do BakersM+!\n\n"
    "Para participar dos grupos de Mythic+, você precisa se cadastrar!\n\n"
    "📌 Informe:\n"
    "➤ Sua **função** (Tank, Healer, DPS)\n"
    "➤ Seu **Nick** do Personagem corretamente!\n"
    "➤ Seu link do **Raider.IO** do seu personagem!\n\n"
    "Use `/cadastrar` para começar!\n"
    "Se você já se cadastrou, use `/perfil` e nos atualize sobre sua disponibilidade."
)

CADASTRO_SUCESSO = (
    "✅ Cadastro concluído! Caso queira cadastrar outro personagem, use o comando `/cadastrar` novamente!"
)

ERRO_CADASTRO = (
    "❌ Erro ao iniciar cadastro. Tente novamente."
)

LIMITE_PERSONAGENS = (
    "❌ Você já atingiu o limite de 4 personagens cadastrados."
)

FUNCAO_INVALIDA = (
    "❌ Função inválida! Use:\n"
    "• Tank (ou tk)\n"
    "• Healer (ou heal)\n"
    "• DPS\n"
)

RATE_LIMIT = (
    "⏳ Aguarde alguns minutos antes de tentar novamente."
)

PERSONAGEM_EXISTENTE = (
    "❌ Este personagem já está cadastrado por outro usuário."
)

RAIDERIO_INVALIDO = (
    "❌ Erro ao validar perfil no Raider.IO. Verifique o link."
)

PERFIL_VAZIO = (
    "❌ Você ainda não registrou nenhum personagem com este ID."
)

CADASTRO_EM_ANDAMENTO = (
    "Você já tem um cadastro em andamento! Complete ou cancele antes de iniciar outro."
)

CADASTRO_CANCELADO = (
    "✅ Cadastro cancelado."
)

PERSONAGEM_REMOVIDO = lambda nome: f"❌ O personagem **{nome}** foi removido do seu perfil."

PERSONAGEM_NAO_ENCONTRADO = (
    "❌ Personagem não encontrado."
)

ERRO_GERAL = (
    "❌ Erro ao processar cadastro. Tente novamente."
)

ERRO_ATUALIZAR_RAIDERIO = (
    "❌ Erro ao atualizar Raider.IO."
)

ERRO_ATUALIZAR_DISPONIBILIDADE = (
    "❌ Erro ao atualizar disponibilidade."
)

ERRO_DELETAR_PERSONAGEM = (
    "❌ Erro ao deletar personagem."
)

ERRO_CARREGAR_PERSONAGEM = (
    "❌ Erro ao carregar personagem."
)

ERRO_INICIAR_CADASTRO = (
    "❌ Erro ao iniciar cadastro. Tente novamente."
)

SISTEMA_SOBRECARGADO = (
    "⚠️ Sistema temporariamente sobrecarregado. Tente novamente em alguns minutos."
)

CADASTRO_CONCLUIDO = (
    "✅ Cadastro concluído! Caso queira cadastrar outro personagem, use o comando `/cadastrar` novamente!"
)

MUITAS_INTERACOES = (
    "⚠️ Muitas interações. Por favor, reinicie o processo."
)

SESSAO_EXPIRADA = (
    "⏰ Esta sessão expirou. Por favor, inicie novamente."
)

SEM_PERMISSAO_VIEW = (
    "🚫 Você não pode interagir com este menu! Use `/cadastrar` para iniciar seu próprio cadastro."
)

AGUARDE_BOTAO = lambda restante: f"⏳ Aguarde {restante} segundos para usar este botão novamente."

AGUARDE_RAIDERIO = lambda restante: f"⏳ Aguarde {restante} segundos para atualizar novamente."

NAO_POSSIVEL_ATUALIZAR_SCORE = (
    "❌ Não foi possível atualizar o score. Verifique o link Raider.IO."
)

LINK_RAIDERIO_NAO_ENCONTRADO = (
    "❌ Link Raider.IO não encontrado para este personagem."
)

# Adicione outros textos conforme necessário...