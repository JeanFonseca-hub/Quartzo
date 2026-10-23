# -*- coding: utf-8 -*-
"""Quartzo - correções aplicadas ao motor UFADE.

Este arquivo é copiado por ferramentas/integrar.py para
motores/ios/quartzo_correcoes.py e chamado pelos ajustes marcados com
"# QUARTZO" no ufade.py gerado. Cada função corresponde a uma falha
encontrada no teste de coleta de 16/10/2026 (ver README, seção "Correções
aplicadas ao motor iOS"). Usa apenas a biblioteca padrão do Python.

Copyright (C) 2026 Jean Carlos Fonseca. Licenciado sob a GNU GPL v3 ou posterior.
"""
import hashlib
import os
import platform
import re
import shutil
import tempfile
import time
import zipfile

# Senha nunca é gravada em log: no lugar dela fica este marcador.
SENHA_OCULTA = "****** (ocultada no log)"

# Estado da sessão do motor. "cripto_previa": True quando o aparelho já tinha
# criptografia de backup ativa antes da extração; False quando foi a
# ferramenta que a ativou; None enquanto não se sabe.
ESTADO = {"cripto_previa": None, "pasta_temp": None}

# Pasta de trabalho onde o backup é copiado a partir do ZIP UFED para a
# descriptografia. Nunca fica dentro da pasta do ZIP de origem nem da pasta da
# sessão (que, quando a descriptografia é feita na mesma sessão da extração,
# é a própria pasta da evidência). Ordem de escolha: variável de ambiente
# QUARTZO_TEMP; pasta temporária do Windows; raiz da mesma unidade do ZIP.
# A primeira com espaço livre suficiente é usada, e a cópia é apagada ao final.
NOME_TEMP_BACKUP = "Quartzo_backup_temporario"
_FOLGA = 1024 ** 3  # 1 GB além do tamanho do backup

_RE_WINDOWS = re.compile(r"[?%*:|\"<>\x7F\x00-\x1F]")
_BLOCO = 16 * 1024 * 1024


# --------------------------------------------------------------- utilidades
def nome_windows(nome, log=None):
    """Troca por '-' os caracteres que o NTFS não aceita (como o motor já faz
    com ':'), inclusive '|'. Só atua no Windows e registra a troca no log."""
    if platform.uname().system != "Windows":
        return nome
    novo = _RE_WINDOWS.sub("-", nome)
    if novo != nome and log is not None:
        log(f"Renamed {nome} to {novo}")
    return novo


def hashes_arquivo(caminho):
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(caminho, "rb") as fh:
        for b in iter(lambda: fh.read(_BLOCO), b""):
            md5.update(b)
            sha1.update(b)
            sha256.update(b)
    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


# ------------------------------------------------ descriptografia do backup
def decifrar(b, file_id, nome_destino, pasta_destino):
    """Descriptografa o registro `file_id` do backup para pasta_destino/nome_destino.

    O registro é localizado pelo fileID, que é único (o caminho relativo se repete
    entre domínios). Em relação ao motor original, a diferença que resta é que os
    arquivos de tamanho zero são gravados: o original não consegue abri-los
    ("cannot mmap an empty file") e só registra que os pulou. As pastas
    intermediárias são criadas aqui. Retorna o caminho gravado.
    """
    entrada = b.getFileManifestDBEntry(fileNameHash=file_id)
    destino = os.path.join(pasta_destino, nome_destino)
    pai = os.path.dirname(destino)
    if pai:
        os.makedirs(pai, exist_ok=True)
    if entrada.get("flags") == 2:  # pasta
        os.makedirs(destino, exist_ok=True)
        return destino
    try:
        b.getFileDecryptedCopy(manifestEntry=entrada, targetName=nome_destino, targetFolder=pasta_destino)
    except ValueError as e:
        if "cannot mmap an empty file" not in str(e):
            raise
        with open(destino, "wb"):
            pass
        try:
            info = type(b).getFileInfo(entrada["manifest"])
            mtime = info["lastModified"].timestamp()
            os.utime(destino, (mtime, mtime))
        except Exception:
            pass
    return destino


def resumo_descriptografia(total, gravados, falhas, log, repetidos=0):
    """Registra no log o balanço da descriptografia. `repetidos` são os registros
    que o PRFS deixa de fora porque o mesmo caminho do aparelho já entrou no ZIP
    a partir de outro domínio (o motor lista cada um como 'Skipped duplicate ZIP entry')."""
    texto = (f"Backup decryption finished: {gravados:,} de {total:,} registros do Manifest.db gravados; "
             f"{falhas:,} falha(s).")
    if repetidos:
        texto += (f" {repetidos:,} registro(s) com caminho já presente no ZIP ficaram de fora "
                  "(listados acima como 'Skipped duplicate ZIP entry').")
    log(texto + " As falhas, se houver, estão listadas acima como 'Error while decrypting file'.")


# ------------------------------------------------ backup a partir do ZIP UFED
_RE_MANIFESTO_ZIP = re.compile(r"^(iPhoneDump/Backup Service/([^/]+)/(?:Snapshot/)?)Manifest\.plist$")


def localizar_backup_ufed(pasta):
    """Procura em `pasta` um ZIP no padrão UFED que contenha um backup iTunes.
    Retorna (caminho_zip, prefixo_no_zip, udid) ou None."""
    try:
        nomes = sorted(os.listdir(pasta))
    except OSError:
        return None
    for nome in nomes:
        if not nome.lower().endswith(".zip"):
            continue
        p = os.path.join(pasta, nome)
        try:
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    m = _RE_MANIFESTO_ZIP.match(n)
                    if m:
                        return p, m.group(1), m.group(2)
        except (zipfile.BadZipFile, OSError):
            continue
    return None


def _dentro(caminho, pasta):
    try:
        caminho, pasta = os.path.normcase(os.path.abspath(caminho)), os.path.normcase(os.path.abspath(pasta))
        return os.path.commonpath([caminho, pasta]) == pasta
    except ValueError:  # unidades diferentes
        return False


def escolher_pasta_temporaria(zip_origem, necessario, log, sessao=None):
    """Escolhe a pasta-base da cópia de trabalho: fora da pasta do ZIP de origem
    e fora da pasta da sessão, com espaço livre para `necessario` bytes + 1 GB.
    Retorna o caminho ou None."""
    sessao = os.path.abspath(sessao or os.getcwd())
    pasta_zip = os.path.dirname(os.path.abspath(zip_origem))
    candidatos = []
    if os.environ.get("QUARTZO_TEMP"):
        candidatos.append(os.environ["QUARTZO_TEMP"])
    candidatos.append(os.path.join(tempfile.gettempdir(), NOME_TEMP_BACKUP))
    unidade = os.path.splitdrive(os.path.abspath(zip_origem))[0]
    if unidade:
        candidatos.append(unidade + os.sep + "_" + NOME_TEMP_BACKUP)
    for c in candidatos:
        c = os.path.abspath(c)
        if _dentro(c, pasta_zip) or _dentro(c, sessao):
            log(f"Pasta temporária {c} recusada: fica dentro da pasta da extração ou da sessão.")
            continue
        try:
            os.makedirs(c, exist_ok=True)
            livre = shutil.disk_usage(c).free
        except OSError as e:
            log(f"Pasta temporária {c} indisponível: {e}")
            continue
        if livre >= necessario + _FOLGA:
            return c
        log(f"Pasta temporária {c} sem espaço suficiente: {livre:,} bytes livres, {necessario + _FOLGA:,} necessários.")
    return None


def preparar_backup_de_zip(pasta, log):
    """Quando a pasta escolhida não é um backup iTunes, mas contém o ZIP UFED da
    extração, copia apenas os metadados do backup (Manifest.plist, Info.plist,
    Status.plist) para uma pasta temporária FORA da pasta da extração e da
    sessão (ver escolher_pasta_temporaria). Retorna (pasta_temp, zip_origem,
    prefixo) ou None."""
    achado = localizar_backup_ufed(pasta)
    if not achado:
        return None
    zip_origem, prefixo, udid = achado
    limpar_temporario(log)  # descarta a cópia de uma escolha anterior nesta sessão, se houver
    with zipfile.ZipFile(zip_origem) as z:
        necessario = sum(i.file_size for i in z.infolist() if i.filename.startswith(prefixo))
    base = escolher_pasta_temporaria(zip_origem, necessario, log)
    if base is None:
        log("Nenhuma pasta temporária com espaço suficiente fora da pasta da extração. Defina a variável "
            "QUARTZO_TEMP com uma pasta de outra unidade ou libere espaço. A cópia de trabalho não foi feita.")
        return None
    raiz_temp = os.path.join(base, f"{udid}_{time.strftime('%Y%m%d_%H%M%S')}")
    ESTADO["pasta_temp"] = raiz_temp
    destino = os.path.join(raiz_temp, udid)
    os.makedirs(destino, exist_ok=True)
    with zipfile.ZipFile(zip_origem) as z:
        for nome in ("Manifest.plist", "Info.plist", "Status.plist"):
            try:
                with z.open(prefixo + nome) as src, open(os.path.join(destino, nome), "wb") as dst:
                    shutil.copyfileobj(src, dst, _BLOCO)
            except KeyError:
                pass
    log(f"Origem do backup: ZIP UFED {zip_origem} (pasta interna {prefixo}). "
        f"Cópia de trabalho em {destino}; o ZIP original é apenas lido.")
    return destino, zip_origem, prefixo


def sha256_registrado_no_ufd(zip_origem):
    """Lê, nos .ufd da mesma pasta, o SHA-256 registrado para o ZIP."""
    pasta, nome_zip = os.path.split(zip_origem)
    for n in os.listdir(pasta):
        if not n.lower().endswith(".ufd"):
            continue
        try:
            with open(os.path.join(pasta, n), "r", encoding="utf-8", errors="replace") as fh:
                for linha in fh:
                    if "=" in linha:
                        chave, valor = linha.strip().split("=", 1)
                        if chave == nome_zip:
                            return valor.strip().lower(), n
        except OSError:
            continue
    return None, None


def extrair_backup_de_zip(zip_origem, prefixo, destino, log, var):
    """Confere o SHA-256 do ZIP com o registrado no .ufd e extrai os arquivos do
    backup para `destino`. var.set(1) em caso de sucesso, var.set(2) em erro."""
    try:
        sha = hashes_arquivo(zip_origem)[2]
        reg, ufd = sha256_registrado_no_ufd(zip_origem)
        if reg:
            situacao = "CONFERE" if reg == sha else "DIVERGE"
            log(f"SHA-256 do ZIP de origem: {sha}; registrado em {ufd}: {reg}; {situacao}.")
        else:
            log(f"SHA-256 do ZIP de origem: {sha} (nenhum .ufd com o hash registrado na pasta).")
        n = 0
        with zipfile.ZipFile(zip_origem) as z:
            for info in z.infolist():
                if info.is_dir() or not info.filename.startswith(prefixo):
                    continue
                rel = info.filename[len(prefixo):]
                if rel in ("Manifest.plist", "Info.plist", "Status.plist"):
                    continue
                alvo = os.path.join(destino, *rel.split("/"))
                os.makedirs(os.path.dirname(alvo), exist_ok=True)
                with z.open(info) as src, open(alvo, "wb") as dst:
                    shutil.copyfileobj(src, dst, _BLOCO)
                n += 1
        log(f"Cópia de trabalho do backup concluída: {n:,} arquivo(s) extraídos do ZIP de origem.")
        var.set(1)
    except Exception as e:
        log(f"Erro ao copiar o backup a partir do ZIP de origem: {e}")
        var.set(2)


def limpar_temporario(log):
    """Apaga a cópia de trabalho do backup, se existir."""
    pasta = ESTADO.get("pasta_temp")
    if pasta and os.path.isdir(pasta):
        shutil.rmtree(pasta, ignore_errors=True)
        situacao = "não pôde ser apagada" if os.path.isdir(pasta) else "apagada"
        log(f"Cópia de trabalho do backup ({pasta}) {situacao}.")
        base = os.path.dirname(pasta)
        try:
            if os.path.basename(base).lstrip("_") == NOME_TEMP_BACKUP and not os.listdir(base):
                os.rmdir(base)
        except OSError:
            pass
    ESTADO["pasta_temp"] = None


def registrar_saida(caminho, log, var):
    """Calcula e registra no log MD5, SHA-1 e SHA-256 do arquivo gerado."""
    try:
        md5, sha1, sha256 = hashes_arquivo(caminho)
        log(f"Arquivo gerado: {os.path.abspath(caminho)} ({os.path.getsize(caminho):,} bytes). "
            f"MD5 {md5}; SHA-1 {sha1}; SHA-256 {sha256}")
    except Exception as e:
        log(f"Erro ao calcular o hash de {caminho}: {e}")
    var.set(1)


# ------------------------------------------- registro de alterações no aparelho
def registrar_cripto_ativada(log):
    ESTADO["cripto_previa"] = False
    log("ALTERAÇÃO NO APARELHO: a criptografia de backup estava DESATIVADA e foi ativada pela ferramenta "
        "com a senha técnica do UFADE, necessária para obter os dados protegidos. A remoção é tentada ao final.")


def registrar_cripto_previa(log, via="informada pelo operador"):
    ESTADO["cripto_previa"] = True
    log(f"A criptografia de backup JÁ ESTAVA ATIVA no aparelho (senha {via}; valor não registrado). "
        "ALTERAÇÃO NO APARELHO: a senha foi trocada temporariamente pela senha técnica do UFADE "
        "e será restaurada ao final.")


def registrar_restauracao(resultado, bu_pass, automatico, log):
    if resultado == 1:
        if bu_pass != "12345":
            log("Senha original de backup do aparelho restaurada. A criptografia de backup permanece ativa, "
                "como estava antes da extração.")
        elif automatico and ESTADO.get("cripto_previa"):
            log("A senha original de backup do aparelho já era a senha técnica do UFADE; "
                "a criptografia foi mantida como estava antes da extração.")
        else:
            log("Senha técnica removida: a criptografia de backup do aparelho foi desativada.")
    else:
        log("FALHA ao remover ou restaurar a senha de backup: o aparelho PERMANECE com a senha técnica "
            "do UFADE. Use a opção de remover a senha de backup e registre o fato no relatório.")


def registrar_pareamento(log):
    log("ALTERAÇÃO NO APARELHO: pareamento (\"Confiar neste computador\") realizado; "
        "o registro de pareamento fica gravado no aparelho e neste computador.")


def registrar_montagem_desenvolvedor(log):
    log("ALTERAÇÃO NO APARELHO: solicitada a montagem da imagem de desenvolvedor (DeveloperDiskImage), "
        "que permanece montada até a reinicialização do aparelho.")


def motivo_interrupcao(codigo):
    motivos = {0: "senha do backup não informada ou não descoberta",
               2: "operação cancelada pelo operador",
               5: "falha durante o backup iTunes"}
    return (f"Logical+ backup interrompido antes da gravação dos arquivos de extração "
            f"({motivos.get(codigo, f'código {codigo}')}). Nenhum arquivo de extração foi gerado nesta tentativa.")


def registrar_compartilhados(app, pasta, log):
    n = tam = 0
    for raiz, _, nomes in os.walk(pasta):
        for nome in nomes:
            n += 1
            try:
                tam += os.path.getsize(os.path.join(raiz, nome))
            except OSError:
                pass
    log(f"Shared app files: {app}: {n} arquivo(s), {tam:,} bytes.")


# ----------------------------------------------------------- CASE/UCO
def completar_case_device(case_device, imei2, lockdown):
    """Acrescenta IMEI 2 e dados do(s) SIM (ICCID, IMSI) ao dicionário do aparelho."""
    try:
        if imei2 and str(imei2).strip():
            case_device["IMEI2"] = str(imei2).strip()
    except Exception:
        pass
    try:
        sims = []
        for entrada in lockdown.all_values.get("CarrierBundleInfoArray") or []:
            iccid = entrada.get("IntegratedCircuitCardIdentity")
            imsi = entrada.get("InternationalMobileSubscriberIdentity")
            if iccid or imsi:
                sims.append({"ICCID": iccid or "", "IMSI": imsi or ""})
        if sims:
            case_device["SIMs"] = sims
    except Exception:
        pass
