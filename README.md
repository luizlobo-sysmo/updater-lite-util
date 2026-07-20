# updater-lite-util — fix do erro 403 XSD Liquibase

Corrige a falha do `updater-lite` na **validação Liquibase**, que deixa a base na versão
antiga. No log (`C:\sysmo-updater\logs\`):

```
java.io.IOException: Server returned HTTP response code: 403
  for URL: http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd
```

## Causa

O liquibase **3.5** embarcado no build resolve XSD **offline** só para as versões que
empacota (`dbchangelog-1.0..3.5` + `dbchangelog-ext`). Os changelogs apontam para
`dbchangelog-latest.xsd`, que **não** está nessa allowlist → o parser busca na rede →
`liquibase.org` devolve **403** → validação falha → update aborta.

Só trocar `-latest` por `-3.5` não basta: o 3.5 é estrito demais e rejeita atributos de
schema mais novo (ex.: `cvc-complex-type.3.2.2 dataType em createSequence`).

## O que o utilitário faz

1. Copia o `build.zip` da versão do share
   `\\192.168.3.5\Versoes S1\build\integracao-continua\pacotes\<branch>\<M.m>\<patch>`
   para `C:\sysmo-updater\upload\pacote` — em **uma passada**, patchando:
   - todos os changelogs `dbchangelog-latest.xsd` → `dbchangelog-3.5.xsd` (nome que o
     resolver 3.5 acha offline);
   - dentro do `liquibase.jar` do build, o **conteúdo** de `dbchangelog-3.5.xsd` (+ `ext`)
     é trocado pelo XSD **permissivo do liquibase 4.x** instalado
     (`C:\SysmoVs\liquibase\bin\internal\lib\liquibase-core.jar`), que aceita os atributos
     novos. Espelha o que a validação online fazia.
2. Dispara o `updater-lite` **SEM** argumento de versão.

> **Importante:** passar a versão (`updater-lite.exe 2.80.03`) faz o updater **re-baixar** o
> pacote do share e **sobrescrever** o `build.zip` patcheado → o erro 403 volta. Sempre no-arg.

## Uso

Dois cliques em `UpdaterLiteUtil.bat` (abre a GUI). Requer **Python 3 com tkinter**
(marque "tcl/tk" e "Add to PATH" no instalador do python.org). Se o Python não for
encontrado, o `.bat` exibe uma mensagem na tela com essas instruções.

Na janela:
- **Branch**: Develop / Release / Master
- **Versão**: ex. `2.80.03`
- **Fix XSD** (marcado por padrão): aplica o patch descrito acima. Desmarcado, apenas copia o
  `build.zip` do share sem alterar e roda o updater (comportamento normal).
- **Iniciar** (valida os campos) / **Cancelar**
- Barra de progresso + log do updater ao vivo. No fim consulta `sgrsis01` e informa a versão.

A base atualizada é a configurada em `C:\SysmoVs\dbxconnections.ini` (`DataBase=`).

## Arquivos

- `UpdaterLiteUtil.py` — GUI + lógica.
- `UpdaterLiteUtil.bat` — wrapper (acha/baixa Python).
