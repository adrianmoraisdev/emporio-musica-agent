from emporio_agent.catalogo import Catalogo, StatusPedido, StatusProduto, diagnosticar


def test_carrega_todas_as_tabelas(catalogo: Catalogo) -> None:
    assert len(catalogo.produtos) == 65
    assert len(catalogo.categorias) == 9
    assert len(catalogo.clientes) == 50
    assert len(catalogo.pedidos) == 20
    assert catalogo.pedidos[15].itens and len(catalogo.pedidos[15].itens) == 2


def test_tipos_convertidos(catalogo: Catalogo) -> None:
    produto = catalogo.produtos[95]
    assert produto.nome.startswith("Takamine GD20")
    assert produto.preco == 2199.0
    assert produto.status is StatusProduto.ATIVO
    assert produto.specs  # JSON de specs parseado
    assert catalogo.pedidos[8].status is StatusPedido.ENVIADO


def test_somente_promocoes_ativas_valem(catalogo: Catalogo) -> None:
    assert set(catalogo.promocoes_ativas) == {90, 94, 121, 127}
    assert catalogo.preco_final(catalogo.produtos[90]) == round(2399 * 0.82, 2)
    # produto 96 só tem promoções inativas
    assert catalogo.preco_final(catalogo.produtos[96]) == 799.9


def test_busca_por_nome_tolerante(catalogo: Catalogo) -> None:
    melhor, _ = catalogo.buscar_por_nome("takamine gd 20")[0]
    assert melhor.id == 95
    melhor, _ = catalogo.buscar_por_nome("Violão Yamaha C40")[0]
    assert melhor.id == 81


def test_disponibilidade(catalogo: Catalogo) -> None:
    assert not catalogo.produtos[96].disponivel  # ativo, estoque 0
    assert not catalogo.produtos[113].disponivel  # descontinuado
    assert not catalogo.produtos[130].disponivel  # em breve
    assert catalogo.produtos[81].disponivel


def test_alternativas_mesma_categoria_disponiveis(catalogo: Catalogo) -> None:
    alternativas = catalogo.alternativas(catalogo.produtos[96])
    assert alternativas
    assert all(p.categoria_id == 5 and p.disponivel for p in alternativas)


def test_verificacao_de_identidade(catalogo: Catalogo) -> None:
    cliente = catalogo.clientes[1]
    assert catalogo.cliente_confere(cliente, "(67) 99812-3456")
    assert catalogo.cliente_confere(cliente, "998123456")
    assert catalogo.cliente_confere(cliente, "LUCAS.MENDES@jmail.com")
    assert catalogo.cliente_confere(cliente, "lucas mendes da silva")
    assert not catalogo.cliente_confere(cliente, "Lucas")
    assert not catalogo.cliente_confere(cliente, "outro@jmail.com")
    assert not catalogo.cliente_confere(cliente, "")


def test_diagnostico_aponta_inconsistencias_conhecidas(catalogo: Catalogo) -> None:
    avisos = "\n".join(diagnosticar(catalogo))
    assert "pedido 3" in avisos
    assert "pedido 20" in avisos
    assert "produto 96" in avisos
    assert "categoria 6" in avisos
    assert "bruno.martins@jmail.com" in avisos


def test_alternativas_priorizam_mesmo_tipo_de_corda(catalogo: Catalogo) -> None:
    esgotado = catalogo.produtos[96]  # Giannini GF-3D, aço
    assert all(
        p.specs.get("strings") == esgotado.specs["strings"] for p in catalogo.alternativas(esgotado)
    )
