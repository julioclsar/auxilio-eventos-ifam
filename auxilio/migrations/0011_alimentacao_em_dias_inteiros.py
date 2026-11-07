# Ajuste manual da alimentação passa de um número com decimais para dias inteiros:
# dias com diária inteira e dias de embarque com meia-diária.

from decimal import Decimal

from django.db import migrations, models


def converter(apps, schema_editor):
    """Leva o ajuste antigo (um total com decimais) para dias inteiros e meias-diárias, sem mudar o valor.

    Igual à regra das datas: fica em branco (vale a regra). Diferente: mantém as meias da regra quando
    a conta fecha (5 diárias numa viagem com 2 embarques = 4 inteiras e 2 meias); senão, 1 meia para a fração.
    """
    Solicitacao = apps.get_model("auxilio", "Solicitacao")
    for s in Solicitacao.objects.exclude(diarias_alimentacao=None):
        total = s.diarias_alimentacao
        dias = (s.viagem_volta - s.viagem_ida).days + 1 if s.viagem_ida and s.viagem_volta else 0
        meias_regra = 0 if dias <= 0 else (1 if dias == 1 else 2)
        inteiras_regra = max(dias - 2, 0) if dias > 1 else 0
        if total == inteiras_regra + Decimal(meias_regra) / 2:
            continue
        resto = total - Decimal(meias_regra) / 2
        if resto >= 0 and resto % 1 == 0:
            s.alim_inteiras, s.alim_meias = int(resto), meias_regra
        else:
            s.alim_inteiras, s.alim_meias = int(total), (1 if total % 1 else 0)
        s.save(update_fields=["alim_inteiras", "alim_meias"])


class Migration(migrations.Migration):

    dependencies = [
        ("auxilio", "0010_rotulo_status_proponente"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitacao",
            name="alim_inteiras",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, verbose_name="Dias com diária inteira"
            ),
        ),
        migrations.AddField(
            model_name="solicitacao",
            name="alim_meias",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, verbose_name="Dias de embarque com meia-diária"
            ),
        ),
        migrations.RunPython(converter, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="solicitacao",
            name="diarias_alimentacao",
        ),
    ]
