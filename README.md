# SDKLaserCapture 0.1.0 · Brawl Stars 69.414

Coletor de diagnóstico com menu nativo para o IPA do link fornecido. O nome interno confirmado é `com.supercell.laser`, versão e build `69.414`, executável `laser`, ARM64, mínimo iOS 14.0. Este é um projeto separado do coletor anterior de Clash Royale.

**Este ZIP contém o coletor em código-fonte e o compilador para GitHub Actions. O framework e o IPA serão gerados pelo workflow. Ainda não houve compilação Apple, assinatura, instalação ou teste no iPhone nesta entrega.**

## Subir no GitHub e compilar

1. Extraia este ZIP no computador. Envie o **conteúdo extraído** para a raiz de um repositório, incluindo `Sources`, `resources`, `tools`, `tests`, `Validation` e `.github`. Não basta subir o ZIP fechado.
2. Confira se existe `.github/workflows/build-laser-capture.yml`. Se o sistema esconder a pasta `.github`, o arquivo `COMPILAR_GITHUB.yml` na raiz é uma cópia: crie o caminho acima no GitHub e cole seu conteúdo. Mantenha apenas esse workflow dentro de `.github/workflows`.
3. Faça o commit na branch padrão. Abra **Actions → Build SDKLaserCapture 0.1.0 → Run workflow**.
4. Deixe `package_ipa` marcado e `base_url` vazio. O fluxo usa o link Drive desta base. Se houver um secret `BASE_IPA_URL`, ele tem prioridade. Você também pode informar outra URL HTTPS que entregue exatamente o mesmo IPA.
5. Ao concluir, baixe o artifact **`SDKLaserCapture-v0.1.0-unsigned-IPA-…`**. Ele contém o IPA e o relatório das alterações.

Nome do IPA previsto:

```text
Brawl_Stars_69.414_SDKLaserCapture_v0.1.0_unsigned.ipa
```

O IPA precisa de assinatura válida do app e dos componentes antes de instalar. Não envie certificados privados, senhas ou arquivos `.p12` ao repositório. O workflow não faz assinatura.

Não precisa subir o IPA de 1,26 GB ao GitHub: o workflow baixa a base e verifica seu tamanho e SHA-256. O download foi realizado nesta sessão e o conteúdo foi conferido, mas a disponibilidade futura do Drive depende do compartilhamento e dos limites do serviço. O downloader para diante de login, CAPTCHA, arquivo incompleto ou hash diferente.

## O que aparece no menu

O botão flutuante **SDK** pode ser arrastado. Durante a captura ele mostra **REC**. O painel contém:

| Botão | Ação |
|---|---|
| Iniciar 90 s | Começa uma sessão manual de diagnóstico |
| Ler agora | Registra um snapshot imediato da estrutura da tela durante a sessão |
| Marcar momento | Acrescenta uma anotação com horário, para relacionar uma jogada ao restante da captura |
| Parar | Encerra a sessão e mantém os dados para exportar |
| Exportar JSON + CSV | Abre o compartilhamento do iOS com o relatório e até cinco tabelas verificadas |
| Fechar painel | Libera a área do jogo, mantendo o botão flutuante |

A interface foi preparada para os dois sentidos de landscape e para o ciclo legado de janelas utilizado por esta base. Isso é uma implementação revisada; o comportamento visual ainda precisa ser confirmado no aparelho.

## Primeira captura

Depois de compilar, assinar e instalar:

1. Entre em uma partida de treino e abra **SDK → Iniciar 90 s**.
2. Feche o painel e jogue manualmente: mova o personagem, mire, ataque, solte os controles e aguarde a resposta visual.
3. Use **Marcar momento** quando quiser indicar um acontecimento. O horário é o toque no marcador, não uma chamada interna do jogo.
4. Aguarde o limite ou toque em **Parar**. Depois use **Exportar JSON + CSV** e escolha **Salvar em Arquivos**.
5. Envie o `capture-….json` e os cinco CSV. No app Arquivos, você pode selecionar esses seis arquivos e compactá-los para enviar juntos. Uma gravação de tela separada ajuda a comparar os horários com o que aconteceu no jogo.

Exporte antes de fechar o app ou iniciar outra sessão. A gravação em andamento fica em memória. Iniciar novamente substitui a sessão anterior. Exportações antigas na pasta interna do coletor são removidas após uma nova exportação concluída; os arquivos já salvos por você permanecem no destino escolhido.

## Dados coletados

**Em execução:** identidade do app e UUID, classes de janelas/views/layers, geometria em pontos, escala, orientação, IDs locais de views e contatos, fases e horários dos toques, marcadores, nomes de classes Objective-C e imagens carregadas do próprio bundle.

**Na exportação:** o coletor lê estas tabelas da base instalada, confere tamanho e SHA-256 e compartilha cópias dos bytes originais:

| Arquivo | Registros com nome após as duas primeiras linhas | Colunas |
|---|---:|---:|
| `characters.csv` | 456 | 184 |
| `skills.csv` | 713 | 106 |
| `projectiles_logic.csv` | 759 | 88 |
| `locations.csv` | 1.357 | 21 |
| `game_mode_variations.csv` | 85 | 154 |

Essas contagens incluem registros que podem estar desativados ou ser internos. Não equivalem ao número de personagens ou modos disponíveis ao jogador. A segunda linha dos CSV contém tipos declarados. Valores vazios são preservados, sem inventar valores padrão.

As cinco tabelas somam 1.172.952 bytes. O JSON informa quais arquivos foram exportados e quais falharam na verificação. O menu pode exportar o diagnóstico mesmo se alguma tabela estiver indisponível.

**Ainda não coletado:** posições dos personagens no mapa, vida, munição, entidades e projéteis ao vivo, chamadas nativas do combate ou estado confirmado de comandos. Esses campos ficam como desconhecidos. `Ler agora` captura a estrutura UIKit, não decodifica os objetos internos de combate. Os CSV descrevem recursos estáticos e não substituem a leitura de uma partida.

Não há autoplay, replay de comandos, extração do código-fonte original, captura de tráfego, textos de campos, senhas, área de transferência, screenshots ou upload automático.

## Limites da sessão

Até 90 segundos, 2.000 eventos, 184 snapshots, 96 views por snapshot, oito janelas e 64 marcadores. A coleta periódica é solicitada a cada 0,5 s; movimentos de cada contato são amostrados a cada 100 ms. O JSON tem limite de 12 MiB. Os CSV têm limite de 1 MiB cada e 2 MiB no total.

A captura para se o app perder atividade ou mostrar teclado. Views de entrada e janelas auxiliares são excluídas, o que pode deixar lacunas. Se a thread principal travar, a parada só será processada quando ela voltar; o relatório guarda o tempo observado. Um toque registrado não prova que um comando foi aceito pelo jogo.

## Arquivos gerados pelo Actions

| Artifact | Conteúdo |
|---|---|
| `…framework…` | Framework compilado, relatório do build e símbolos opcionais |
| `…unsigned-IPA…` | Novo IPA sem assinatura válida e relatório das mudanças |
| `…static-tables…` | ZIP com os cinco CSV e relatório da extração estática |
| `…diagnostics…` | Logs e verificações, inclusive em falhas de build |

Os artifacts são mantidos por sete dias. O workflow tem permissão de leitura no repositório e não publica releases. No primeiro build, a verificação de fontes e os testes rodam antes da compilação.

Se falhar, envie o artifact `diagnostics` e o link do run. Use **Run workflow** no commit atualizado depois de uma correção; executar novamente um run antigo pode usar o código antigo.

## Conferir uma captura no computador

Requer Python 3.12; nenhum pacote externo precisa ser instalado.

```bash
python3 tools/analyze_capture.py capture-SESSAO.json --output resumo-SESSAO.json
```

O resumo verifica identidade, tempos, contatos, limites e metadados dos arquivos. Ele não reproduz toques e não confirma os bytes de um CSV que não foi fornecido junto. Para verificar um CSV no PowerShell:

```powershell
Get-FileHash .\characters.csv -Algorithm SHA256
```

Compare com `resources/AssetIndex.json` ou com o hash esperado no JSON da captura.

## Compilar em um Mac

Use Xcode completo com SDK iPhoneOS e Python 3.12. `dist` não pode existir antes do build.

```bash
python3 tools/verify_sources.py
python3 -m unittest discover -s tests -v
python3 tools/build_framework.py --output dist
python3 tools/fetch_base.py --output work/base.ipa
python3 tools/package_ipa.py \
  --base work/base.ipa \
  --framework dist/SDKLaserCapture.framework \
  --output dist/Brawl_Stars_69.414_SDKLaserCapture_v0.1.0_unsigned.ipa
```

Para extrair apenas as cinco tabelas da base original, sem executar o aplicativo:

```bash
python3 tools/export_tables.py --base work/base.ipa --output dist/Brawl_Stars_69.414_Tabelas.zip
```

Detalhes de compilação em `tools/APPLE_BUILD.md`. Em Windows/Linux, você pode preparar o repositório e analisar os relatórios. O script recusa a compilação UIKit fora do macOS.

## Base e integração

| Identificador | Valor |
|---|---|
| Bundle | `com.supercell.laser` |
| Versão/build | `69.414` |
| UUID principal | `15a0e3e2-2688-43f1-80df-50ce637685bd` |
| Tamanho do IPA | 1.256.288.047 bytes |
| SHA-256 do IPA | `d3229b7e15b28fbcfc8fe784cd466f671252e6ebe3712f5354b9a58ce98bc0d8` |
| SHA-256 do executável original | `19b031a23bae706a93f6f3a89bb1bb40103ceedc5871f90fc8d2d4f624058d52` |

O empacotador exige essa base e um framework Apple real. Adiciona uma dependência no espaço livre conferido do cabeçalho Mach-O. Preserva as dependências existentes, os recursos e os componentes do aplicativo, incluindo `laserx`. O IPA original não é sobrescrito.

A assinatura original fica inválida após a integração. Não foi demonstrado que a base modificada instala ou abre. As proteções existentes são preservadas; este pacote não as contorna.

`Validation/STATUS.json` distingue testes portáteis, inspeção da base, extração estática, compilação Apple e execução no aparelho. Os testes com fixtures não são capturas reais.

`SOURCE_MANIFEST.json` registra os hashes dos arquivos entregues. Se alterar intencionalmente os fontes, revise as mudanças e atualize-o com `python3 tools/verify_sources.py --write`. Não regenere o manifesto só para esconder uma diferença inesperada.
