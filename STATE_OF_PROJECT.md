# Estado Atual do Projeto: Interfone AI (08/04/2026)

Este documento resume o estado atual do sistema após a restauração da qualidade de áudio e melhorias no PWA. Use este arquivo como base para futuras iterações.

## 🚀 Resumo Técnico
O sistema foi restaurado para um estado de alta estabilidade de áudio, eliminando artefatos "robóticos" e lentidão, e o PWA foi otimizado para notificações sonoras e visuais confiáveis no Android.

---

## 📟 Firmware ESP32 (main/app_main.c)
**Estado:** FUNCIONAL E ESTÁVEL.
- **Áudio:** O driver I2S agora é gerenciado dinamicamente pelo ESP-ADF.
- **⚠️ REGRA DE OURO:** NUNCA reinstalar `i2s_driver_install` no `app_main` e nunca usar `uninstall_drv = false` nos streams. Isso causava o travamento em 8kHz (áudio lento).
- **Configuração Atual:**
  - Player: Abre na taxa solicitada pelo servidor (24kHz para áudios locais, 8kHz para vozes).
  - Canal: Configurado como `ONLY_RIGHT` (Mono) para evitar que o áudio toque 2x mais rápido (voz fina).
- **Repositório:** [https://github.com/kekabab/interfone](https://github.com/kekabab/interfone)

---

## 🌐 Servidor Backend (server/server.py)
**Estado:** DEPLOYED NO RENDER.
- **Logica de Chamada:** Agora mantém a sessão ativa por **40 segundos** após o acionamento do interfone.
- **Botões do PWA:** Permite múltiplos cliques nos botões de resposta rápida durante a janela de 40 segundos.
- **Transcrição:** Utiliza OpenAI Whisper-1 para converter o áudio do visitante (8kHz mono) em texto.
- **Injeção de Silêncio:** Envia 400ms de silêncio antes das respostas para "acordar" o amplificador/DAC da placa sem cortar o início da fala.

---

## 📱 PWA Morador (server/static/index.html)
**Estado:** VERSÃO V3.
- **Áudio (Campainha):** Gerado localmente via Web Audio API (Onda Triângulo) para ser audível mesmo em volumes baixos e não depender de links externos.
- **Notificações:** Suporte a vibração intensa e notificações de sistema no Chrome/Android.
- **Interface:** 
  - Selo **V3** no topo para confirmar cache atualizado.
  - Texto de transcrição grande e destacado em branco.
  - Botão **"Testar Som da Campainha"** no rodapé para diagnóstico.
- **Service Worker:** Atualizado para `sw.js` v3 para forçar limpeza de cache.

---

## 🛠️ Como Continuar
1. **Código Base:** Sempre use o código do branch `main` do GitHub acima.
2. **Deploy:** O Render faz deploy automático ao receber `push` no GitHub.
3. **Build ESP32:** Use os scripts `do_build.bat` e `do_flash.bat` no ambiente local.

**Assinado:** Antigravity (IA)
