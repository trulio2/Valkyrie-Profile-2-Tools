/* SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
 * SPDX-License-Identifier: GPL-3.0-only
 */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "tac_lib.c"

#define POINTS (TAC_CODED_BANDS * TAC_CODED_COEFS)
#define PAIR_SAMPLES (TAC_FRAME_SAMPLES * 2)
#define MAX_SYMBOLS (TAC_MAX_CODES * TAC_CHANNELS)
#define TAC_FORMAT_MAX_PAYLOAD 0x7FFF

typedef struct
{
    uint8_t *data;
    size_t size;
    size_t capacity;
} bytes_t;

typedef struct
{
    uint16_t *symbols;
    uint16_t count;
    uint8_t use_history;
} frame_codes_t;

static unsigned long adjusted_bands;
static size_t largest_payload;

static int read_file(const char *path, uint8_t **data, size_t *size)
{
    FILE *file = fopen(path, "rb");
    long length;
    if (!file)
        return 0;
    if (fseek(file, 0, SEEK_END) || (length = ftell(file)) < 0 ||
        fseek(file, 0, SEEK_SET))
    {
        fclose(file);
        return 0;
    }
    *data = malloc((size_t)length);
    if (!*data || fread(*data, 1, (size_t)length, file) != (size_t)length)
    {
        free(*data);
        fclose(file);
        return 0;
    }
    fclose(file);
    *size = (size_t)length;
    return 1;
}

static int read_floats(const char *path, float **data, size_t count)
{
    size_t size;
    uint8_t *raw = NULL;
    if (!read_file(path, &raw, &size) || size < count * sizeof(float))
    {
        free(raw);
        return 0;
    }
    *data = (float *)raw;
    return 1;
}

static void put_u16le(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put_u32le(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static int bytes_push(bytes_t *out, uint8_t value)
{
    if (out->size == out->capacity)
    {
        size_t capacity = out->capacity ? out->capacity * 2 : 1024;
        uint8_t *data = realloc(out->data, capacity);
        if (!data)
            return 0;
        out->data = data;
        out->capacity = capacity;
    }
    out->data[out->size++] = value;
    return 1;
}

static void normalize_encode(uint32_t *lower, uint32_t *range, bytes_t *out)
{
    while ((*lower ^ (*lower + *range)) <= 0xFFFFFFu)
    {
        bytes_push(out, (uint8_t)(*lower >> 24));
        *range <<= 8;
        *lower <<= 8;
    }
    while (*range <= 0xFFFFu)
    {
        bytes_push(out, (uint8_t)(*lower >> 24));
        *range = (((~*lower) + 1) & 0xFFFFu) << 8;
        *lower <<= 8;
    }
}

static int range_encode(const uint16_t *frequencies,
                        const uint16_t *cumulative, const uint16_t *symbols, int count,
                        bytes_t *out)
{
    uint32_t lower = 0, range = 0xFFFFFFFFu;
    out->size = 0;
    for (int i = 0; i < count; i++)
    {
        uint32_t value = symbols[i], base, extra = 0;
        int extended = 0;
        if (value < 0xFE)
            base = value;
        else if (value < 0x1FE)
        {
            base = 0xFE;
            extra = value - 0xFE;
            extended = 1;
        }
        else
        {
            base = 0xFF;
            extra = value;
            extended = 2;
        }
        if (!frequencies[base])
            return 0;
        range >>= 14;
        lower += cumulative[base] * range;
        range *= frequencies[base];
        normalize_encode(&lower, &range, out);
        if (extended)
        {
            range >>= extended == 1 ? 8 : 13;
            lower += extra * range;
            normalize_encode(&lower, &range, out);
        }
    }
    return bytes_push(out, (uint8_t)(lower >> 24)) &&
           bytes_push(out, (uint8_t)(lower >> 16)) &&
           bytes_push(out, (uint8_t)(lower >> 8)) &&
           bytes_push(out, (uint8_t)lower);
}

static uint16_t unsigned_symbol(int value)
{
    return (uint16_t)(value < 0 ? -2 * value - 1 : 2 * value);
}

static unsigned base_symbol(unsigned value)
{
    if (value < 0xFE)
        return value;
    if (value < 0x1FE)
        return 0xFE;
    return 0xFF;
}

static int build_frequency_model(const frame_codes_t *frames, int frame_count,
                                 uint16_t *frequencies, uint16_t *cumulative)
{
    uint64_t counts[256] = {0}, total = 0;
    int most_common = 0;
    for (int frame = 0; frame < frame_count; frame++)
    {
        for (int i = 0; i < frames[frame].count; i++)
        {
            unsigned symbol = base_symbol(frames[frame].symbols[i]);
            counts[symbol]++;
            total++;
        }
    }
    if (!total)
        return 0;
    unsigned sum = 0;
    for (int symbol = 0; symbol < 256; symbol++)
    {
        if (counts[symbol] > counts[most_common])
            most_common = symbol;
        if (!counts[symbol])
        {
            frequencies[symbol] = 0;
            continue;
        }
        uint64_t scaled = counts[symbol] *
                          (TAC_RANGE_FREQUENCY_SIZE - 1) / total;
        if (!scaled)
            scaled = 1;
        frequencies[symbol] = (uint16_t)scaled;
        sum += frequencies[symbol];
    }
    while (sum > TAC_RANGE_FREQUENCY_SIZE - 1)
    {
        int largest = -1;
        for (int symbol = 0; symbol < 256; symbol++)
        {
            if (frequencies[symbol] > 1 &&
                (largest < 0 || frequencies[symbol] > frequencies[largest]))
                largest = symbol;
        }
        if (largest < 0)
            return 0;
        frequencies[largest]--;
        sum--;
    }
    frequencies[most_common] += (uint16_t)(TAC_RANGE_FREQUENCY_SIZE - 1 - sum);
    frequencies[256] = 1;
    cumulative[0] = 0;
    for (int symbol = 0; symbol < 257; symbol++)
        cumulative[symbol + 1] = cumulative[symbol] + frequencies[symbol];
    return cumulative[257] == TAC_RANGE_FREQUENCY_SIZE;
}

static int write_frequency_model(uint8_t *output, size_t output_size,
                                 size_t offset, const uint16_t *frequencies)
{
    for (int symbol = 0; symbol < 256; symbol++)
    {
        uint16_t frequency = frequencies[symbol];
        if (frequency < 0x80)
        {
            if (offset >= output_size)
                return -1;
            output[offset++] = (uint8_t)frequency;
        }
        else
        {
            if (offset + 1 >= output_size)
                return -1;
            output[offset++] = (uint8_t)((frequency & 0x7F) | 0x80);
            output[offset++] = (uint8_t)(frequency >> 7);
        }
    }
    return (int)offset;
}

static float decode_value(int code, float scale)
{
    float magnitude, raw, lower, upper, fraction, value;
    int index;
    if (!code)
        return 0.0f;
    magnitude = fabsf((float)code) * scale;
    raw = magnitude * 512.0f;
    index = (int)raw;
    if (index < 0)
        index = 0;
    if (index > 511)
        index = 511;
    lower = (float)index * 0.00195313f;
    upper = (float)(index + 1) * 0.00195313f;
    fraction = (magnitude - lower) / (upper - lower);
    value = SCALE_TABLE[index].f.x + fraction *
                                         (SCALE_TABLE[index + 1].f.x - SCALE_TABLE[index].f.x);
    return code < 0 ? -value : value;
}

static int quantize_value(float value, float scale)
{
    float magnitude = fabsf(value), fraction, estimate, span;
    int lo = 0, hi = 511, index, center, best = 0;
    float best_error = fabsf(value);
    while (lo + 1 < hi)
    {
        int mid = (lo + hi) / 2;
        if (SCALE_TABLE[mid].f.x <= magnitude)
            lo = mid;
        else
            hi = mid;
    }
    index = lo > 510 ? 510 : lo;
    span = SCALE_TABLE[index + 1].f.x - SCALE_TABLE[index].f.x;
    fraction = span == 0.0f ? 0.0f : (magnitude - SCALE_TABLE[index].f.x) / span;
    estimate = (index + fraction) / (512.0f * scale);
    center = (int)lrintf(estimate);
    if (center > 4095)
        center = 4095;
    for (int candidate = center - 3; candidate <= center + 3; candidate++)
    {
        int signed_code;
        float error;
        if (candidate < 0 || candidate > 4095)
            continue;
        signed_code = value < 0 ? -candidate : candidate;
        error = fabsf(decode_value(signed_code, scale) - value);
        if (error < best_error)
        {
            best_error = error;
            best = signed_code;
        }
    }
    return best;
}

static float estimated_code(float value, float scale)
{
    float magnitude = fabsf(value), span, fraction;
    int lo = 0, hi = 511, index;
    while (lo + 1 < hi)
    {
        int mid = (lo + hi) / 2;
        if (SCALE_TABLE[mid].f.x <= magnitude)
            lo = mid;
        else
            hi = mid;
    }
    index = lo > 510 ? 510 : lo;
    span = SCALE_TABLE[index + 1].f.x - SCALE_TABLE[index].f.x;
    fraction = span == 0.0f ? 0.0f : (magnitude - SCALE_TABLE[index].f.x) / span;
    return (index + fraction) / (512.0f * scale);
}

static int parse_wav(const uint8_t *data, size_t size,
                     const int16_t **samples, size_t *sample_frames)
{
    size_t pos = 12;
    int format_ok = 0;
    if (size < 12 || memcmp(data, "RIFF", 4) || memcmp(data + 8, "WAVE", 4))
        return 0;
    while (pos + 8 <= size)
    {
        uint32_t length = get_u32le(data + pos + 4);
        size_t body = pos + 8;
        if (body + length > size)
            return 0;
        if (!memcmp(data + pos, "fmt ", 4) && length >= 16)
        {
            format_ok = get_u16le(data + body) == 1 &&
                        get_u16le(data + body + 2) == 2 &&
                        get_u32le(data + body + 4) == 48000 &&
                        get_u16le(data + body + 14) == 16;
        }
        else if (!memcmp(data + pos, "data", 4) && format_ok)
        {
            *samples = (const int16_t *)(data + body);
            *sample_frames = length / 4;
            return 1;
        }
        pos = body + length + (length & 1);
    }
    return 0;
}

static float target_sample(const int16_t *pcm, size_t frames,
                           int frame, int channel, int sample, int joint, long sync_samples,
                           long long output_samples)
{
    double output_index = (double)frame * TAC_FRAME_SAMPLES + sample - sync_samples;
    double source_index = output_index * frames / output_samples;
    long long index = (long long)floor(source_index);
    float left = 0.0f, right = 0.0f;
    if (index >= 0 && (size_t)index < frames)
    {
        size_t next = (size_t)index + 1 < frames
                          ? (size_t)index + 1
                          : (size_t)index;
        float fraction = (float)(source_index - index);
        left = pcm[(size_t)index * 2] * (1.0f - fraction) +
               pcm[next * 2] * fraction;
        right = pcm[(size_t)index * 2 + 1] * (1.0f - fraction) +
                pcm[next * 2 + 1] * fraction;
    }
    if (!joint)
        return channel ? right : left;
    return channel ? (left - right) * 0.5f : (left + right) * 0.5f;
}

static void analyze(const float *analysis, const float *overlap,
                    const float *previous, const int16_t *pcm, size_t pcm_frames,
                    int frame, int channel, int joint, long sync_samples,
                    long long output_samples, float *spectrum)
{
    float rhs[PAIR_SAMPLES];
    for (int sample = 0; sample < TAC_FRAME_SAMPLES; sample++)
    {
        double baseline = 0.0;
        for (int coef = 0; coef < POINTS; coef++)
            baseline += overlap[coef * TAC_FRAME_SAMPLES + sample] * previous[coef];
        rhs[sample] = target_sample(
                          pcm, pcm_frames, frame, channel, sample, joint, sync_samples,
                          output_samples) -
                      (float)baseline;
        rhs[TAC_FRAME_SAMPLES + sample] = target_sample(
            pcm, pcm_frames, frame + 1, channel, sample, joint,
            sync_samples, output_samples);
    }
    for (int coef = 0; coef < POINTS; coef++)
    {
        const float *row = analysis + (size_t)coef * PAIR_SAMPLES;
        double total = 0.0;
        for (int sample = 0; sample < PAIR_SAMPLES; sample++)
            total += row[sample] * rhs[sample];
        spectrum[coef] = (float)total;
    }
}

static int choose_band_scale(const float *input, int band, int base_scale)
{
    float largest = 0.0f;
    for (int i = 0; i < TAC_CODED_COEFS; i++)
    {
        float value = fabsf(input[band * TAC_CODED_COEFS + i]);
        if (value > largest)
            largest = value;
    }
    if (largest <= 1.0e-9f)
        return 0;

    int low = 1, high = 383, best = 1;
    while (low <= high)
    {
        int middle = low + (high - low) / 2;
        float scale = SCALE_TABLE[128 + middle].f.y *
                      SCALE_TABLE[base_scale].f.y;
        if (estimated_code(largest, scale) <= 252.0f)
        {
            best = middle;
            low = middle + 1;
        }
        else
        {
            high = middle - 1;
        }
    }
    return best;
}

static void derive_scales(const float *input, const int16_t *previous_scales,
                          int max_active, int16_t *scales)
{
    double energy[TAC_CODED_BANDS] = {0};
    double priority[TAC_CODED_BANDS] = {0};
    float largest[TAC_CODED_BANDS] = {0};
    int enabled[TAC_CODED_BANDS] = {0};
    int active = 0;
    double strongest_energy = 0.0;
    double energy_floor = max_active <= 9 ? 1.6e-4 : 8.0e-5;
    memset(scales, 0, 28 * sizeof(*scales));

    for (int band = 0; band < TAC_CODED_BANDS; band++)
    {
        for (int i = 0; i < TAC_CODED_COEFS; i++)
        {
            float value = input[band * TAC_CODED_COEFS + i];
            float magnitude = fabsf(value);
            energy[band] += (double)value * value;
            if (magnitude > largest[band])
                largest[band] = magnitude;
        }
        if (energy[band] > strongest_energy)
            strongest_energy = energy[band];
    }
    for (int band = 0; band < TAC_CODED_BANDS; band++)
    {
        if (largest[band] > 1.0e-9f &&
            energy[band] >= strongest_energy * energy_floor)
        {
            enabled[band] = 1;
            active++;
        }
        priority[band] = energy[band] *
                         (previous_scales[band + 1] ? 1.25 : 1.0);
    }
    while (max_active > 0 && active > max_active)
    {
        int weakest = -1;
        for (int band = 0; band < TAC_CODED_BANDS; band++)
        {
            if (enabled[band] &&
                (weakest < 0 || priority[band] < priority[weakest]))
                weakest = band;
        }
        if (weakest < 0)
            break;
        enabled[weakest] = 0;
        active--;
    }
    if (!active)
        return;

    scales[0] = 0;
    for (int band = 0; band < TAC_CODED_BANDS; band++)
    {
        if (!enabled[band])
            continue;
        int selected = choose_band_scale(input, band, 0);
        int previous = previous_scales[band + 1];
        if (previous > 0 && previous <= selected)
        {
            if (selected > previous + 1)
                selected = previous + 1;
            else
                selected = previous;
        }
        scales[band + 1] = (int16_t)selected;
    }
}

static int quantize_channel(const float *input,
                            const int16_t *previous_scales, int scale_bias,
                            int max_active, int16_t *codes, float *spectrum)
{
    int cursor = 28;
    derive_scales(input, previous_scales, max_active, codes);
    for (int band = 1; band <= TAC_CODED_BANDS; band++)
    {
        if (codes[band] > 0)
        {
            if (scale_bias != 99)
                codes[band] += scale_bias;
            if (codes[band] < 1)
                codes[band] = 1;
            if (codes[band] > 383)
                codes[band] = 383;
        }
    }
    memset(spectrum, 0, POINTS * sizeof(*spectrum));
    for (int band = 0; band < TAC_CODED_BANDS; band++)
    {
        int band_scale = codes[band + 1];
        if (!band_scale)
            continue;
        float scale;
        if (scale_bias == 99)
        {
            int original = band_scale;
            while (band_scale > 1)
            {
                float trial = SCALE_TABLE[128 + band_scale].f.y *
                              SCALE_TABLE[codes[0]].f.y;
                float largest = 0.0f;
                for (int i = 0; i < TAC_CODED_COEFS; i++)
                {
                    float estimate = estimated_code(
                        input[band * TAC_CODED_COEFS + i], trial);
                    if (estimate > largest)
                        largest = estimate;
                }
                if (largest <= 252.0f)
                    break;
                band_scale--;
            }
            codes[band + 1] = (int16_t)band_scale;
            if (band_scale != original)
                adjusted_bands++;
        }
        scale = SCALE_TABLE[128 + band_scale].f.y *
                SCALE_TABLE[codes[0]].f.y;
        for (int i = 0; i < TAC_CODED_COEFS; i++)
        {
            int pos = band * TAC_CODED_COEFS + i;
            int code = quantize_value(input[pos], scale);
            if (cursor >= TAC_MAX_CODES)
                return 0;
            codes[cursor++] = (int16_t)code;
            spectrum[pos] = decode_value(code, scale);
        }
    }
    return cursor;
}

static int write_stream(const char *path, uint8_t *output, size_t size)
{
    FILE *file = fopen(path, "wb");
    int ok = file && fwrite(output, 1, size, file) == size;
    if (file)
        fclose(file);
    return ok;
}

int main(int argc, char **argv)
{
    uint8_t *template = NULL, *wav = NULL, *output = NULL;
    size_t template_size = 0, wav_size = 0, pcm_frames = 0;
    const int16_t *pcm = NULL;
    float *analysis = NULL, *overlap = NULL;
    tac_handle_t *decoder = NULL;
    float previous[TAC_CHANNELS][POINTS] = {{0}};
    float solved[TAC_CHANNELS][POINTS], quantized[TAC_CHANNELS][POINTS];
    int16_t encoded[TAC_CHANNELS][TAC_MAX_CODES];
    int16_t histories[TAC_CHANNELS][28] = {{0}};
    frame_codes_t *frames = NULL;
    bytes_t payload = {0};
    int output_block = 0, output_offset;
    int output_frame_count, output_frame_last, joint;
    long long output_samples;
    size_t capacity, output_size, used_end, raw_end, logical_size;
    int scale_bias, max_active;
    long sync_samples, sync_frames;
    if (argc != 11)
    {
        fprintf(stderr, "usage: tac_encode template.laac input.wav pair.bin overlap.bin output.laac capacity scale-bias max-active sync-frames output-samples\n");
        return 2;
    }
    scale_bias = atoi(argv[7]);
    max_active = atoi(argv[8]);
    sync_frames = strtol(argv[9], NULL, 10);
    sync_samples = sync_frames * TAC_FRAME_SAMPLES;
    output_samples = strtoll(argv[10], NULL, 10);
    if (output_samples <= 0)
        return 2;
    output_frame_count = (int)((output_samples + TAC_FRAME_SAMPLES - 1) /
                               TAC_FRAME_SAMPLES);
    output_frame_last = (int)((output_samples - 1) % TAC_FRAME_SAMPLES);
    capacity = (size_t)strtoull(argv[6], NULL, 10);
    if (!read_file(argv[1], &template, &template_size) ||
        !read_file(argv[2], &wav, &wav_size) ||
        !parse_wav(wav, wav_size, &pcm, &pcm_frames) ||
        !read_floats(argv[3], &analysis, (size_t)POINTS * PAIR_SAMPLES) ||
        !read_floats(argv[4], &overlap, (size_t)POINTS * TAC_FRAME_SAMPLES))
    {
        fprintf(stderr, "could not read encoder inputs\n");
        return 3;
    }
    decoder = tac_init(template, (int)template_size);
    if (!decoder)
    {
        fprintf(stderr, "invalid TAC template\n");
        return 4;
    }
    joint = decoder->header.joint_stereo;
    output_size = ((capacity + TAC_BLOCK_SIZE - 1) / TAC_BLOCK_SIZE) *
                  TAC_BLOCK_SIZE;
    output = malloc(output_size);
    frames = calloc((size_t)output_frame_count, sizeof(*frames));
    if (!output || !frames)
        return 5;
    memset(output, 0xFF, output_size);
    fprintf(stderr, "encoding %d TAC frames from WAV with sync %+.6fs",
            output_frame_count, sync_samples / 48000.0);

    for (int frame = 0; frame < output_frame_count; frame++)
    {
        int use_history = frame % 64 != 0;
        int code_counts[TAC_CHANNELS];
        int channel_limits[TAC_CHANNELS];
        int16_t saved_histories[TAC_CHANNELS][28];
        uint16_t symbols[MAX_SYMBOLS];
        int symbol_count = 0;
        for (int ch = 0; ch < TAC_CHANNELS; ch++)
        {
            analyze(analysis, overlap, previous[ch], pcm, pcm_frames,
                    frame, ch, joint, sync_samples, output_samples, solved[ch]);
            channel_limits[ch] = max_active;
        }
        memcpy(saved_histories, histories, sizeof(histories));
        memcpy(histories, saved_histories, sizeof(histories));
        for (int ch = 0; ch < TAC_CHANNELS; ch++)
        {
            code_counts[ch] = quantize_channel(
                solved[ch], saved_histories[ch], scale_bias,
                channel_limits[ch], encoded[ch], quantized[ch]);
            if (!code_counts[ch])
                return 7;
            for (int i = 0; i < code_counts[ch]; i++)
            {
                int value = encoded[ch][i];
                if (i < 28)
                {
                    int absolute = value;
                    if (use_history)
                        value -= histories[ch][i];
                    histories[ch][i] = (int16_t)absolute;
                }
                symbols[symbol_count++] = unsigned_symbol(value);
            }
        }
        for (int ch = 0; ch < TAC_CHANNELS; ch++)
            memcpy(previous[ch], quantized[ch], sizeof(previous[ch]));
        frames[frame].symbols = malloc(
            (size_t)symbol_count * sizeof(*frames[frame].symbols));
        if (!frames[frame].symbols)
            return 5;
        memcpy(frames[frame].symbols, symbols,
               (size_t)symbol_count * sizeof(*symbols));
        frames[frame].count = (uint16_t)symbol_count;
        frames[frame].use_history = (uint8_t)use_history;
        if ((frame + 1) % 256 == 0)
        {
            fputc('.', stderr);
            fflush(stderr);
        }
    }

    if (!build_frequency_model(
            frames, output_frame_count, decoder->symbol_frequency,
            decoder->cumulative_frequency))
    {
        fprintf(stderr, "\ncould not build TAC range model\n");
        return 8;
    }
    memcpy(output, template, decoder->header.range_offset);
    output_offset = write_frequency_model(
        output, output_size, decoder->header.range_offset,
        decoder->symbol_frequency);
    if (output_offset < 0 || output_offset > TAC_BLOCK_SIZE)
    {
        fprintf(stderr, "\nTAC range model exceeds first block\n");
        return 8;
    }

    for (int frame = 0; frame < output_frame_count; frame++)
    {
        int symbol_count = frames[frame].count;
        if (!range_encode(
                decoder->symbol_frequency, decoder->cumulative_frequency,
                frames[frame].symbols, symbol_count, &payload))
            return 8;
        if (payload.size > TAC_FORMAT_MAX_PAYLOAD)
        {
            fprintf(stderr, "\nframe %d exceeds TAC packet size\n",
                    frame + 1);
            return 8;
        }
        if (payload.size > largest_payload)
            largest_payload = payload.size;
        size_t packet_size = 8 + payload.size;
        if ((size_t)output_offset + packet_size + 4 > TAC_BLOCK_SIZE)
        {
            output_block++;
            output_offset = 0;
        }
        size_t absolute = (size_t)output_block * TAC_BLOCK_SIZE + output_offset;
        if (absolute + packet_size > output_size)
        {
            fprintf(stderr, "\nencoded TAC exceeds logical template size\n");
            return 9;
        }
        uint8_t *packet = output + absolute;
        uint16_t flags = (uint16_t)payload.size |
                         (frames[frame].use_history ? 0x8000 : 0);
        put_u16le(packet + 2, flags);
        put_u16le(packet + 4, (uint16_t)(frame + 1));
        put_u16le(packet + 6, (uint16_t)symbol_count);
        memcpy(packet + 8, payload.data, payload.size);
        put_u16le(packet, crc16(packet + 4, (int)payload.size + 4));
        output_offset += (int)packet_size;
    }
    raw_end = (size_t)output_block * TAC_BLOCK_SIZE + output_offset;
    logical_size = (size_t)(output_block + 1) * TAC_BLOCK_SIZE;
    used_end = raw_end;
    size_t minimum = logical_size - TAC_BLOCK_SIZE;
    if (used_end < minimum)
        used_end = minimum;
    if (used_end > capacity)
    {
        fprintf(stderr, "\nencoded stream needs %zu bytes; capacity is %s\n", used_end, argv[6]);
        return 10;
    }
    put_u16le(output + 0x0C, (uint16_t)output_frame_count);
    put_u16le(output + 0x0E, (uint16_t)output_frame_last);
    put_u32le(output + 0x10, (uint32_t)logical_size);
    put_u32le(output + 0x14, (uint32_t)logical_size);
    if (!write_stream(argv[5], output, used_end))
    {
        fprintf(stderr, "\ncould not write %s: %s\n", argv[5], strerror(errno));
        return 11;
    }
    fprintf(stderr, "\nwrote %zu bytes (packets %zu) in %d block(s); adjusted %lu band(s); largest payload %zu\n",
            used_end, raw_end, output_block + 1, adjusted_bands,
            largest_payload);
    for (int frame = 0; frame < output_frame_count; frame++)
        free(frames[frame].symbols);
    free(frames);
    free(payload.data);
    free(output);
    tac_free(decoder);
    free(analysis);
    free(overlap);
    free(wav);
    free(template);
    return 0;
}
