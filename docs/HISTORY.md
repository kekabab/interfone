# Histórico e Configurações do Projeto: ESP32 AI Intercom

Este documento serve como a base de conhecimento (Source of Truth) para o estado atual do projeto, facilitando o trabalho de futuras IAs ou desenvolvedores.

## 1. Visão Geral do Sistema
O sistema consiste em um interfone baseado em **ESP32-A1S** que se comunica via **SIP** com o servidor **SignalWire**, mediado por uma **Inteligência Artificial (Whisper)** que transcreve a fala do visitante para rotear chamadas.

## 2. Configurações de Software e Hardware

### ESP32 (Firmware)
- **Plataforma**: ESP-ADF v2.x / ESP-IDF v4.2.5
- **Hardware**: ESP32-A1S (Audio Kit)
- **SIP Endpoint**: `interfone` (registrado na SignalWire)
- **SIP Domain**: `beraaa-eb70df85408e.sip.signalwire.com`
- **Senha PBX**: `InterfoneAi123!`

### Servidor de IA (Python)
- **Script**: `server/server.py` (rodando em Python local)
- **Porta**: 8765
- **IA**: Whisper (Local) para transcrição de áudio.
- **Lógica de Roteamento**: 
  - Expressões como "mauricio", "maur", "rício" -> Discagem para ramal `mauricio`.
  - Expressões como "claudia", "cláudia" ou falha no reconhecimento -> Fallback para `claudia`.

## 3. Linha do Tempo de Correções (Março/2026)

### Sincronia de Áudio e Sample Rate (Correção Crítica)
- **Problema**: O TTS tocava a 24k, mas o SIP esperava 16k ou 8k. O codec não mudava a taxa a tempo, resultando em ruído ou silêncio.
- **Solução**: Modificado o handler `SIP_EVENT_AUDIO_SESSION_BEGIN` no `app_main.c` para forçar o reset do I2S:
  ```c
  i2s_set_sample_rates(I2S_PORT, 16000);
  i2s_zero_dma_buffer(I2S_PORT);
  ```

### Verificação de Estado SIP
- **Problema**: O ESP32 tentava discar antes de o registro na SignalWire ser concluído.
- **Solução**: Alterado o check no `websocket_event_handler` para:
  `if (esp_sip_uac_get_state(sip) < SIP_STATE_REGISTERED)`.

### Ambiente de Build
- **Script**: `do_build.bat` foi limpo de argumentos incompatíveis (removido `-j 1`).

## 4. Estado Atual e Bloqueios
- **SIP**: Funcionando 100% (invites, registro e áudio de entrada).
- **Áudio de Saída (TX)**: Melhorado com o ajuste de sample rate.
- **Noise Suppression (NS)**: Implementação iniciada com `algorithm_stream`, mas bloqueada por erros de link de biblioteca FFT (`esp_kiss_fft`).
  - *Dica para o próximo*: Verificar a inclusão correta do componente `esp-sr` e se há necessidade de habilitar as bibliotecas matemáticas no Kconfig.

---
**Documento gerado em 25/03/2026 por Antigravity.**
