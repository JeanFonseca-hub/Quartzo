# -*- coding: utf-8 -*-
"""
Quartzo - ajustes de integração aplicados aos motores depois da tradução.

Cada ajuste é localizado por um trecho exato do código original (os espaços
no fim das linhas são ignorados na comparação); se o autor do UFADE/ALEX mudar
esse trecho, a geração para com erro em vez de aplicar algo no lugar errado.

Integração (UFADE e ALEX):
1. Aviso de modificação no cabeçalho (exigência da GPL-3.0, seção 5a).
2. Dados do caso (nº, nome, item/lacre, responsável) vindos do lançador por
   variáveis de ambiente, para pré-preencher relatórios PDF/UFDR.
3. Espelhamento do log do motor no log de sessão do Quartzo.
4. Sonda de progresso (quartzo_progresso.py): copia para a janela
   "Acompanhamento da extração" o que a barra de progresso do motor já exibe.

Correções do motor iOS (UFADE), a partir do teste de coleta de 16/10/2026.
O código das correções fica em quartzo_app/correcoes_ufade.py, copiado para
motores/ios/quartzo_correcoes.py; aqui ficam só os pontos de chamada.
C1. Descriptografia por registro do Manifest.db (fileID), e não por caminho
    relativo: o original gravava só a cópia do primeiro app quando o mesmo
    caminho existia em vários apps, descartava arquivos de tamanho zero e era
    quadrático no número de arquivos. Afeta Logical+ (TAR), PRFS (ZIP) e as
    opções "Descriptografar" e "Descriptografar e reconstruir backup".
C2. Resumo da descriptografia no log (total, gravados, falhas).
C3. Interrupção correta do Logical+ quando a senha não é obtida, o operador
    cancela ou o backup falha (antes o motor seguia e gravava um ZIP vazio).
C4. Senha de backup ocultada no log.
C5. Registro, no log, das alterações feitas no aparelho (criptografia de
    backup ativada/restaurada, pareamento, imagem de desenvolvedor); a senha
    original "12345" não é mais removida ao final.
C6. AFC: nomes de pasta com caracteres inválidos no Windows (como "|")
    passam a ser trocados por "-", como o motor já fazia com ":".
C7. Fuso horário do .ufd e do UFDR: "(-3)" em vez de "(--3)".
C8. ZIP padrão UFED gravado sem compressão (STORED): o conteúdo já é
    criptografado, a compressão não reduzia o tamanho e só tornava gravação e
    leitura mais lentas.
C9. Arquivos compartilhados de apps: contagem por app no log; no modo PRFS os
    arquivos não eram gravados no ZIP (usava a pasta no lugar do arquivo).
C10. CASE/UCO: prefixos ausentes no @context; IMEI 2 e SIM (ICCID/IMSI).
C11. "Reconstruir/Descriptografar backup" aceita a pasta do .ufd/ZIP UFED:
     o backup é copiado para uma pasta temporária FORA da pasta da extração e
     da sessão (QUARTZO_TEMP, pasta temporária do Windows ou raiz da unidade,
     a primeira com espaço); o ZIP de origem é só lido e tem o SHA-256
     conferido com o .ufd; ao final, a cópia é apagada e os hashes do arquivo
     gerado vão para o log.
"""
import json
import os
import re

# O aviso cita o commit e a data do original, e não o dia da geração: assim duas
# gerações feitas sobre o mesmo original saem idênticas byte a byte.
AVISO = ("# ---------------------------------------------------------------------------\n"
         "# QUARTZO: arquivo MODIFICADO em relação ao original de Christian Peter.\n"
         "# Modificações geradas por ferramentas/traduzir.py e ferramentas/integrar.py\n"
         "# sobre o commit {commit} do original ({data}): textos de interface e de\n"
         "# relatório PDF traduzidos para pt-BR; dados do caso lidos do lançador; log\n"
         "# espelhado na sessão Quartzo; sonda de progresso (quartzo_progresso.py) para\n"
         "# a janela de acompanhamento; no motor iOS, correções de falhas da extração\n"
         "# marcadas com \"# QUARTZO\" (quartzo_correcoes.py; lista completa em\n"
         "# ferramentas/integrar.py).\n"
         "# ---------------------------------------------------------------------------\n")

CASO_ORIG = ('case_number = ""\ncase_name = ""\nevidence_number = ""\nexaminer = ""\n')
CASO_NOVO = ('case_number = os.environ.get("QUARTZO_CASO_NUMERO", "")  # QUARTZO\n'
             'case_name = os.environ.get("QUARTZO_CASO_NOME", "")  # QUARTZO\n'
             'evidence_number = os.environ.get("QUARTZO_EVIDENCIA", "")  # QUARTZO\n'
             'examiner = os.environ.get("QUARTZO_EXAMINADOR", "")  # QUARTZO\n')

LOG_ESPELHO = ('def log(text):\n'
               '    try:  # QUARTZO: espelha o evento no log de sessão do lançador\n'
               '        _quartzo_log = os.environ.get("QUARTZO_SESSAO_LOG")\n'
               '        if _quartzo_log:\n'
               '            with open(_quartzo_log, "a", encoding="utf-8") as _quartzo_f:\n'
               '                _quartzo_f.write(f"[{{datetime.now().astimezone().isoformat(timespec=\'seconds\')}}] [{motor}] {{text}}\\n")\n'
               '    except Exception:\n'
               '        pass\n')

SONDA_ORIG = "\nimport customtkinter as ctk\n"
SONDA_NOVA = "\nimport customtkinter as ctk\nimport quartzo_progresso  # QUARTZO: sonda da janela de acompanhamento\n"

QC = "quartzo_correcoes"

# --------------------------------------------------------------------------
# Correções do motor iOS. Cada item: (arquivo, trecho original, trecho novo[, ocorrências]).
# --------------------------------------------------------------------------
CORRECOES_UFADE = [
    # importação do módulo de correções
    ("ufade.py",
     "import quartzo_progresso  # QUARTZO: sonda da janela de acompanhamento\n",
     "import quartzo_progresso  # QUARTZO: sonda da janela de acompanhamento\n"
     f"import {QC}  # QUARTZO: correções do motor (ver ferramentas/integrar.py)\n"),

    # C5 - criptografia de backup ativada pela ferramenta
    ("ufade.py",
     '                Mobilebackup2Service(lockdown).change_password(new="12345")\n'
     '            change.set(1)\n'
     '        except Exception as e:\n'
     '            e = str(e)\n'
     '            if "device is locked" in e or "PasswordProtected" in e:\n'
     '                change.set(3)\n'
     '            elif "Cannot parse a NULL" in str(e):\n'
     '                change.set(1)\n',
     '                Mobilebackup2Service(lockdown).change_password(new="12345")\n'
     f'            {QC}.registrar_cripto_ativada(log)  # QUARTZO\n'
     '            change.set(1)\n'
     '        except Exception as e:\n'
     '            e = str(e)\n'
     '            if "device is locked" in e or "PasswordProtected" in e:\n'
     '                change.set(3)\n'
     '            elif "Cannot parse a NULL" in str(e):\n'
     f'                {QC}.registrar_cripto_ativada(log)  # QUARTZO\n'
     '                change.set(1)\n'),

    # C5 - restauração da senha ao final (e registro do resultado)
    ("ufade.py",
     '            else:\n'
     '                UFADEMobilebackup2Service(lockdown).change_password(old="12345")\n'
     '            change.set(1)\n'
     '        except:\n'
     '            change.set(2)\n'
     '        if text != None:\n',
     f'            elif text is not None and {QC}.ESTADO.get("cripto_previa"):\n'
     '                pass  # QUARTZO: a senha original do aparelho já era a senha técnica; mantém como estava\n'
     '            else:\n'
     '                UFADEMobilebackup2Service(lockdown).change_password(old="12345")\n'
     '            change.set(1)\n'
     '        except:\n'
     '            change.set(2)\n'
     f'        {QC}.registrar_restauracao(change.get(), bu_pass, text is not None, log)  # QUARTZO\n'
     '        if text != None:\n'),

    # C4/C5 - senha ocultada no log e registro de criptografia prévia
    ("ufade.py",
     'log(f"Provided correct backup password: {pw}")',
     f'log(f"Provided correct backup password: {{{QC}.SENHA_OCULTA}}"); {QC}.registrar_cripto_previa(log)  # QUARTZO',
     2),
    ("ufade.py",
     'log(f"Provided incorrect backup password: {pw} or device error (MDM)")',
     f'log(f"Provided incorrect backup password: {{{QC}.SENHA_OCULTA}} or device error (MDM)")  # QUARTZO'),
    ("ufade.py",
     'log(f"Found correct backup password: {pw} via bruteforce")',
     f'log(f"Found correct backup password: {{{QC}.SENHA_OCULTA}} via bruteforce (exibida na tela)"); '
     f'{QC}.registrar_cripto_previa(log, via="descoberta por força bruta")  # QUARTZO'),

    # C3 - falha do backup iTunes sinalizada a quem chamou
    ("ufade.py",
     '                self.change.set(0)\n'
     '                return()\n'
     '        else:\n'
     '            pass\n'
     '\n'
     '# Advanced Logical Backup Fuctions\n',
     '                self.change.set(0)\n'
     '                self.pw_found.set(5)  # QUARTZO: sinaliza a falha do backup a quem chamou\n'
     '                return()\n'
     '        else:\n'
     '            pass\n'
     '\n'
     '# Advanced Logical Backup Fuctions\n'),

    # C3 - Logical+ só prossegue com o backup concluído
    ("ufade.py",
     '            self.perf_iTunes_bu(bu_name)\n'
     '            if self.pw_found.get() == 2:\n'
     '                return()\n',
     '            self.perf_iTunes_bu(bu_name)\n'
     '            if self.pw_found.get() != 1:  # QUARTZO: antes só o código 2 interrompia; com 0 o motor seguia e gravava um ZIP vazio\n'
     f'                log({QC}.motivo_interrupcao(self.pw_found.get()))\n'
     '                if self.pw_found.get() == 0:\n'
     '                    self.switch_menu("AcqMenu")\n'
     '                return()\n'),

    # C1 - lista de arquivos (Logical+/PRFS)
    ("ufade.py",
     '                    line_list = []\n'
     '                    line_cnt = 0\n'
     "                    for line in backupfiles['relativePath']:\n"
     '                        if(line not in line_list):\n'
     '                            line_cnt += 1\n'
     '                            line_list.append(line)\n',
     f'                    line_list = {QC}.lista_backup(backupfiles)  # QUARTZO: um item por registro do Manifest.db\n'
     '                    line_cnt = len(line_list)\n'),
    # C1 - lista de arquivos (Descriptografar / Reconstruir backup)
    ("ufade.py",
     '            line_list = []\n'
     '            line_cnt = 0\n'
     "            for line in backupfiles['relativePath']:\n"
     '                if(line not in line_list):\n'
     '                    line_cnt += 1\n'
     '                    line_list.append(line)\n',
     f'            line_list = {QC}.lista_backup(backupfiles)  # QUARTZO: um item por registro do Manifest.db\n'
     '            line_cnt = len(line_list)\n'),

    # C1/C2 - decrypt_itunes
    ("ufade.py",
     '        for file in line_list:\n'
     '            fileout = file\n'
     "            if platform.uname().system == 'Windows':\n",
     '        _qz_total = len(line_list)  # QUARTZO\n'
     '        _qz_falhas = 0\n'
     '        for _qz_id, filedomain, file in line_list:  # QUARTZO: (fileID, domínio, caminho relativo)\n'
     '            fileout = file\n'
     "            if platform.uname().system == 'Windows':\n"),
    ("ufade.py",
     "            filedomain = backupfiles.loc[backupfiles['relativePath'] == file, 'domain'].iloc[0]\n"
     '            try:\n'
     '                b.getFileDecryptedCopy(relativePath=file, targetName=fileout, targetFolder=os.path.join(".tar_tmp", "itunes_bu"))               #actually decrypt the backup-files\n',
     '            try:\n'
     f'                {QC}.decifrar(b, _qz_id, fileout, os.path.join(".tar_tmp", "itunes_bu"))  # QUARTZO: pelo fileID do registro\n'),
    ("ufade.py",
     "backupfiles.loc[backupfiles['relativePath'] == file, 'domain'].iloc[0], file), recursive=False)",
     "filedomain, file), recursive=False)  # QUARTZO: domínio do próprio registro"),
    ("ufade.py",
     '            except Exception as e:\n'
     '                if "cannot mmap an empty file" not in str(e):\n'
     "                    log(f\"Error while decrypting file:{file.encode('cp1252', errors='ignore').decode('cp1252')} - {e}\")\n"
     '        change.set(1)\n'
     '\n'
     "# Decrypt, don't unback\n",
     '            except Exception as e:\n'
     '                _qz_falhas += 1  # QUARTZO: arquivos vazios agora são gravados; toda falha é registrada\n'
     "                log(f\"Error while decrypting file:{filedomain}/{file.encode('cp1252', errors='ignore').decode('cp1252')} - {e}\")\n"
     f'        {QC}.resumo_descriptografia(_qz_total, _qz_total - _qz_falhas, _qz_falhas, log)  # QUARTZO\n'
     '        change.set(1)\n'
     '\n'
     "# Decrypt, don't unback\n"),

    # C1 - only_decrypt_itunes
    ("ufade.py",
     '        for file in line_list:\n'
     '            fileout = file\n'
     '            d_nr += 1\n',
     '        for _qz_id, _qz_dom, file in line_list:  # QUARTZO: (fileID, domínio, caminho relativo)\n'
     '            fileout = file\n'
     '            d_nr += 1\n'),
    ("ufade.py",
     "            sha_name = backupfiles.loc[backupfiles['relativePath'] == file, 'backupFile'].iloc[0]\n",
     '            sha_name = _qz_id  # QUARTZO: o fileID do próprio registro\n'),
    ("ufade.py",
     '                b.getFileDecryptedCopy(relativePath=file, targetName=sha_name, targetFolder=target_dir)\n',
     f'                {QC}.decifrar(b, sha_name, sha_name, target_dir)  # QUARTZO\n'),

    # C11 - Reconstruir/Descriptografar a partir da pasta do .ufd/ZIP UFED
    ("ufade.py",
     '        manifest = os.path.join(bu_f, "Manifest.plist")\n'
     '        if os.path.isfile(manifest):\n',
     '        self.qz_zip_origem = None  # QUARTZO: aceita a pasta do .ufd/ZIP UFED, sem gravar nela\n'
     '        if not os.path.isfile(os.path.join(bu_f, "Manifest.plist")):\n'
     f'            _qz_prep = {QC}.preparar_backup_de_zip(bu_f, log)\n'
     '            if _qz_prep:\n'
     '                bu_f, self.qz_zip_origem, self.qz_zip_prefixo = _qz_prep\n'
     '        manifest = os.path.join(bu_f, "Manifest.plist")\n'
     '        if os.path.isfile(manifest):\n'),
    ("ufade.py",
     '        self.change = ctk.IntVar(self, 0)\n'
     '        panda_backup = threading.Thread(target=lambda: self.init_backup_decrypt(self.change, udid=bu_udid, bu_pass=bu_pass, backuproot=bu_root))\n',
     '        if getattr(self, "qz_zip_origem", None):  # QUARTZO: copia o backup do ZIP UFED para uma pasta temporária fora da extração\n'
     '            self.text.configure(text="Conferindo o hash do ZIP de origem e copiando o backup para uma pasta\\n"\n'
     '                                     "temporária fora da pasta da extração. O ZIP original não é alterado. Isso pode demorar.")\n'
     '            self.text.update()\n'
     '            _qz_var = ctk.IntVar(self, 0)\n'
     f'            threading.Thread(target=lambda: {QC}.extrair_backup_de_zip(self.qz_zip_origem, self.qz_zip_prefixo, bu_folder, log, _qz_var), daemon=True).start()\n'
     '            self.wait_variable(_qz_var)\n'
     '            if _qz_var.get() != 1:\n'
     f'                {QC}.limpar_temporario(log)\n'
     '                self.text.configure(text="Não foi possível copiar o backup a partir do ZIP de origem.\\nConsulte o log da sessão.")\n'
     '                self.text.update()\n'
     '                self.after(100, lambda: ctk.CTkButton(self.dynamic_frame, text="OK", font=self.stfont, command=lambda: self.switch_menu("Data")).pack(pady=40))\n'
     '                return\n'
     '        self.change = ctk.IntVar(self, 0)\n'
     '        panda_backup = threading.Thread(target=lambda: self.init_backup_decrypt(self.change, udid=bu_udid, bu_pass=bu_pass, backuproot=bu_root))\n'),
    ("ufade.py",
     '                zip.close()\n'
     '                try: shutil.rmtree(".tar_tmp")\n'
     '                except: pass\n'
     '            else:\n',
     '                zip.close()\n'
     '                try: shutil.rmtree(".tar_tmp")\n'
     '                except: pass\n'
     f'                {QC}.limpar_temporario(log)  # QUARTZO: apaga a cópia de trabalho e registra os hashes da saída\n'
     '                self.text.configure(text="Calculando os hashes do arquivo gerado. Isso pode demorar.")\n'
     '                self.text.update()\n'
     '                _qz_var = ctk.IntVar(self, 0)\n'
     f"                threading.Thread(target=lambda: {QC}.registrar_saida(f'{{zipname}}.zip', log, _qz_var), daemon=True).start()\n"
     '                self.wait_variable(_qz_var)\n'
     '                self.text.configure(text=f"Backup iTunes descriptografado com sucesso.\\nSaída: {zipname}.zip")\n'
     '            else:\n'),
    ("ufade.py",
     '                self.after(10, lambda: self.text.configure(text=f"Backup iTunes descriptografado com sucesso.\\nSaída: {outfolder}"))\n',
     '                self.after(10, lambda: self.text.configure(text=f"Backup iTunes descriptografado com sucesso.\\nSaída: {outfolder}"))\n'
     f'                {QC}.limpar_temporario(log)  # QUARTZO\n'),
    ("ufade.py",
     '            self.text.configure(text="Ocorreu um erro.\\nConfira a senha do backup.")\n',
     f'            {QC}.limpar_temporario(log)  # QUARTZO\n'
     '            self.text.configure(text="Ocorreu um erro.\\nConfira a senha do backup.")\n'),

    # C6 - AFC: nomes de pasta inválidos no Windows
    ("ufade.py",
     '            dst_path = pathlib.Path(dst) / os.path.basename(relative_src)\n',
     f'            dst_path = pathlib.Path(dst) / {QC}.nome_windows(os.path.basename(relative_src), log)  # QUARTZO\n'),
    ("ufade.py",
     '                dst_filename = dst_path / filename\n',
     f'                dst_filename = dst_path / {QC}.nome_windows(filename)  # QUARTZO\n'),

    # C7 - fuso horário no .ufd e no UFDR
    ("ufade.py",
     'sign + str(int(utc_offset_hours))',
     'sign + str(abs(int(utc_offset_hours)))',
     4),

    # C8 - ZIP padrão UFED sem compressão
    ("ufade.py",
     "            case_json_name = f'{zipname}.case.json'\n"
     '            zip = zipfile.ZipFile(f\'{zipname}.zip\', "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1)\n'
     '            tar = None\n'
     '            self.after(100, lambda: self.text.configure(text="Processando o backup. Isso pode demorar."))\n',
     "            case_json_name = f'{zipname}.case.json'\n"
     '            zip = zipfile.ZipFile(f\'{zipname}.zip\', "w", compression=zipfile.ZIP_STORED)  # QUARTZO: conteúdo já criptografado\n'
     '            tar = None\n'
     '            self.after(100, lambda: self.text.configure(text="Processando o backup. Isso pode demorar."))\n'),

    # C9 - arquivos compartilhados de apps
    ("ufade.py",
     '                pull(self=HouseArrestService(lockdown, bundle_id=app, documents_only=True), relative_src="/Documents/.", dst=file_path)\n',
     '                pull(self=HouseArrestService(lockdown, bundle_id=app, documents_only=True), relative_src="/Documents/.", dst=file_path)\n'
     f'                {QC}.registrar_compartilhados(app, file_path, log)  # QUARTZO\n'),
    ("ufade.py",
     '                                if app_arc not in unback_set and os.path.isfile(file_path):\n'
     '                                    zip.write(file_path, app_arc)\n',
     '                                if app_arc not in unback_set and os.path.isfile(source_file):  # QUARTZO: era file_path (a pasta)\n'
     '                                    zip.write(source_file, app_arc)\n'),

    # C10 - IMEI 2 e SIM no CASE/UCO
    ("ufade.py",
     '                if imei and imei.strip():\n'
     '                    case_device["IMEI"] = imei\n',
     '                if imei and imei.strip():\n'
     '                    case_device["IMEI"] = imei\n'
     f'                {QC}.completar_case_device(case_device, globals().get("imei2"), lockdown)  # QUARTZO\n'),

    # C5 - pareamento e imagem de desenvolvedor
    ("ufade.py",
     '        global ispaired\n'
     '        ispaired = True\n'
     '        paired.set(True)\n',
     '        global ispaired\n'
     '        ispaired = True\n'
     f'        {QC}.registrar_pareamento(log)  # QUARTZO\n'
     '        paired.set(True)\n',
     2),
    ("ufade.py",
     '    def mount_developer(self, change, text):\n'
     '        global developer\n',
     '    def mount_developer(self, change, text):\n'
     '        global developer\n'
     f'        {QC}.registrar_montagem_desenvolvedor(log)  # QUARTZO\n'),

    # C10 - case_uco.py
    ("ufade/case_uco.py",
     '    "uco-types": "https://ontology.unifiedcyberontology.org/uco/types/",\n',
     '    "uco-types": "https://ontology.unifiedcyberontology.org/uco/types/",\n'
     '    "uco-action": "https://ontology.unifiedcyberontology.org/uco/action/",  # QUARTZO: prefixos usados no grafo\n'
     '    "uco-tool": "https://ontology.unifiedcyberontology.org/uco/tool/",\n'
     '    "uco-configuration": "https://ontology.unifiedcyberontology.org/uco/configuration/",\n'
     '    "drafting": "http://example.org/ontology/drafting/",\n'),
    ("ufade/case_uco.py",
     '    md_facet["drafting:localeLanguage"] = case_device["localeLanguage"]\n',
     '    if case_device.get("IMEI2"):  # QUARTZO: segundo IMEI\n'
     '        md_facet["drafting:IMEI2"] = case_device["IMEI2"]\n'
     '    md_facet["drafting:localeLanguage"] = case_device["localeLanguage"]\n'),
    ("ufade/case_uco.py",
     '    backup_case.append(device)\n',
     '    backup_case.append(device)\n'
     '    for sim in case_device.get("SIMs", []):  # QUARTZO: cartão SIM (ICCID/IMSI) contido no aparelho\n'
     '        sim_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, sim.get("ICCID") or sim.get("IMSI") or str(uuid.uuid4()))\n'
     '        sim_facet = {"@id": "kb:sim-card-facet-" + str(uuid.uuid4()), "@type": "uco-observable:SIMCardFacet"}\n'
     '        if sim.get("ICCID"):\n'
     '            sim_facet["uco-observable:ICCID"] = sim["ICCID"]\n'
     '        if sim.get("IMSI"):\n'
     '            sim_facet["uco-observable:IMSI"] = sim["IMSI"]\n'
     '        sim_obj = {"@id": "kb:sim-card-" + str(sim_uuid), "@type": "uco-observable:SIMCard",\n'
     '                   "uco-core:hasFacet": [sim_facet]}\n'
     '        backup_case.append(sim_obj)\n'
     '        backup_case.append({\n'
     '            "@id": "kb:relationship-" + str(uuid.uuid5(uuid.NAMESPACE_DNS, str(device_uuid) + str(sim_uuid))),\n'
     '            "@type": "uco-observable:ObservableRelationship",\n'
     '            "uco-core:kindOfRelationship": "Contained_Within",\n'
     '            "uco-core:isDirectional": True,\n'
     '            "uco-core:source": {"@id": sim_obj["@id"]},\n'
     '            "uco-core:target": {"@id": device["@id"]},\n'
     '        })\n'),
]

AJUSTES = {
    "ufade": [
        ("ufade.py", "# Licensed under GPLv3 License\n", "# Licensed under GPLv3 License\n{aviso}"),
        ("ufade.py", CASO_ORIG, CASO_NOVO),
        ("ufade.py", "def log(text):\n    try: log_udid = udid\n",
         LOG_ESPELHO.format(motor="UFADE") + "    try: log_udid = udid\n"),
        ("ufade.py", SONDA_ORIG, SONDA_NOVA),
    ] + CORRECOES_UFADE,
    "alex": [
        ("alex.py", "# Licensed under GPLv3 License\n", "# Licensed under GPLv3 License\n{aviso}"),
        ("alex.py", CASO_ORIG, CASO_NOVO),
        ("alex.py", "def log(text):\n    with open(f\"ALEX_log_{snr}.log\"",
         LOG_ESPELHO.format(motor="ALEX") + "    with open(f\"ALEX_log_{snr}.log\""),
        ("alex.py", SONDA_ORIG, SONDA_NOVA),
    ],
}

# arquivos do Quartzo copiados para dentro de cada motor
COPIAS = {
    "ufade": [("sonda_progresso.py", "quartzo_progresso.py"), ("correcoes_ufade.py", "quartzo_correcoes.py")],
    "alex": [("sonda_progresso.py", "quartzo_progresso.py")],
}


def _padrao(trecho):
    """Expressão regular que casa o trecho exato, tolerando espaços/tabulações
    no fim de cada linha (o código original tem muitos)."""
    linhas = trecho.split("\n")
    return re.compile("".join(re.escape(l.rstrip(" \t")) + ("[ \t]*\n" if i < len(linhas) - 1 else "")
                              for i, l in enumerate(linhas)))


def _origem(raiz, motor):
    """(data, commit) do original registrados em upstream/ORIGEM.json."""
    nome = {"ufade": "UFADE", "alex": "ALEX"}[motor]
    try:
        with open(os.path.join(raiz, "upstream", "ORIGEM.json"), "r", encoding="utf-8") as fh:
            d = json.load(fh).get(nome, {})
    except (OSError, ValueError):
        d = {}
    return (d.get("data_commit") or "data desconhecida")[:10], (d.get("commit") or "desconhecido")[:12]


def aplicar(motor, destino):
    import shutil
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data, commit = _origem(raiz, motor)
    for origem, nome in COPIAS[motor]:
        shutil.copyfile(os.path.join(raiz, "quartzo_app", origem), os.path.join(destino, nome))
    fontes = {}
    for item in AJUSTES[motor]:
        arquivo, antigo, novo = item[:3]
        esperado = item[3] if len(item) > 3 else 1
        caminho = os.path.join(destino, arquivo)
        if caminho not in fontes:
            with open(caminho, "r", encoding="utf-8") as fh:
                fontes[caminho] = fh.read()
        fonte = fontes[caminho]
        padrao = _padrao(antigo)
        n = len(padrao.findall(fonte))
        if n != esperado:
            raise SystemExit(f"ERRO de integração em {arquivo}: trecho esperado {esperado} vez(es), "
                             f"encontrado {n}:\n{antigo[:120]!r}\n"
                             "O código original mudou; revise ferramentas/integrar.py.")
        texto_novo = novo.replace("{aviso}", AVISO.format(data=data, commit=commit))
        fontes[caminho] = padrao.sub(lambda m: texto_novo, fonte)
    for caminho, fonte in fontes.items():
        compile(fonte, caminho, "exec")
        with open(caminho, "w", encoding="utf-8", newline="") as fh:
            fh.write(fonte)
    return len(AJUSTES[motor])
