# Build Apple e download da base

O pacote contém fontes e ferramentas. O workflow precisa concluir no macOS antes de existir um framework compilado ou um IPA modificado. O build não assina nem instala o aplicativo.

## GitHub Actions

Adicione o conteúdo extraído do ZIP na raiz do repositório, incluindo `.github/workflows/build-laser-capture.yml`. Coloque o workflow na branch padrão para habilitar o botão de execução manual.

Abra **Actions → Build SDKLaserCapture 0.1.0 → Run workflow**. Mantenha `package_ipa` marcado para gerar o IPA. O campo `base_url` aceita uma URL HTTPS da mesma base exata. Se vazio, é usado o secret `BASE_IPA_URL`; se o secret também estiver vazio, é usado o link Drive fornecido pelo usuário para esta base.

O workflow recebe a URL por variável de ambiente, sem interpolá-la em código shell. O downloader não imprime a URL. Ele aceita uma confirmação comum de download do Drive, mas interrompe em login, CAPTCHA, confirmação repetida, hash diferente ou arquivo incompleto. Um link novo precisa entregar os mesmos bytes fixados em `resources/Target.json`.

São produzidos quatro artifacts separados:

* `SDKLaserCapture-v0.1.0-framework-…`: framework compilado em ZIP, relatório e dSYM opcional.
* `SDKLaserCapture-v0.1.0-unsigned-IPA-…`: IPA sem assinatura válida e relatório das alterações, somente após integração bem-sucedida.
* `SDKLaserCapture-v0.1.0-diagnostics-…`: logs e verificações, inclusive quando uma etapa falhar.
* `SDKLaserCapture-v0.1.0-static-tables-…`: cinco CSV verificados e relatório, extraídos da base quando `package_ipa` está marcado. Esses dados são estáticos.

Os artifacts são mantidos por sete dias. O ZIP interno do framework preserva a estrutura e o modo executável do binário. O upload usa `compression-level: 0` para evitar a recompressão externa de IPA/ZIP; isso não garante velocidade da rede ou do servidor de download.

## Mac local

Requisitos: Xcode completo selecionado, SDK iPhoneOS, Apple clang e Python 3.12. A pasta de saída não pode existir.

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

Para usar o IPA já baixado, passe o caminho diretamente a `package_ipa.py --base`. `fetch_base.py --local /caminho/base.ipa --output work/base.ipa` também verifica e copia a base sem substituí-la.

## O que é verificado

O compilador é obtido por `xcrun --sdk iphoneos`. Todos os `.m` são compilados separadamente para ARM64, iOS 14.0, com ARC, blocks e informações de debug. O link inclui UIKit, Foundation, QuartzCore e libobjc, com install name `@rpath/SDKLaserCapture.framework/SDKLaserCapture`. O produto é validado pelo mesmo verificador usado no empacotamento.

O relatório registra os hashes das entradas, a versão do Xcode, do SDK e do Apple clang, os comandos e seus retornos. Os objetos permanecem disponíveis até a geração opcional do dSYM. Uma mudança nas entradas durante a compilação impede a publicação. Nenhum binário antigo de `dist` é reaproveitado.

O download é limitado pelo tamanho fixado da base; todos os membros do ZIP são lidos para verificar CRC. A publicação do IPA baixado não substitui um arquivo existente. O empacotador faz a validação adicional da identidade, dos caminhos e da estrutura Mach-O.

Em Linux o build retorna código 2, sem criar um framework ou pasta `dist`. Testes Python e revisão de fontes não comprovam compilação Apple nem execução no iPhone. Após gerar o IPA, ainda é necessária uma assinatura válida do app e dos componentes, seguida de teste real no dispositivo.

## Referências de configuração

Conferidas em 21/10/2026:

* https://docs.github.com/en/actions/reference/runners/github-hosted-runners
* https://github.com/actions/checkout
* https://github.com/actions/setup-python
* https://github.com/actions/upload-artifact

O workflow usa `macos-15`, `actions/checkout@v5`, `actions/setup-python@v5` e `actions/upload-artifact@v4`. Essas versões foram escolhidas para este pacote; não são apresentadas como os majors mais recentes.
